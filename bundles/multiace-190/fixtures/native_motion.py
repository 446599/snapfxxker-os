class NativeMotion:
    def _tick(self):
        """Work through the feed events, then catch a piece whose end
        event was lost (the lane idle in the report, or the deadline)."""
        self._work_events()
        now = self._now()
        self._flog_advance(now)
        fresh = self._fresh(self._feed)
        modes = bytearray(self._feed[1].get('mode', b'')) if fresh else b''
        for lane in list(self._jobs):
            job = self._jobs[lane]
            if job.get('waiting'):
                if job.get('stopping'):
                    self._follow_want.discard(lane)
                    job['stopped'] = True
                    self._end_job(lane, 'cancelled before start', stop='stopped')
                    continue
                # A start the unit refused as busy (its tie-break).
                limit = job['deadline']
                if now >= limit:
                    self._follow_want.discard(lane)
                    self._end_job(lane, 'start refused (busy)', stop='busy')
                    continue
                res = self._start_piece(job)
                if res == 'ok':
                    job.pop('waiting', None)
                    logging.info(self._lp + '%s lane %d started after '
                                 'the unit freed the lane'
                                 % (job['kind'], lane))
                elif res != 'busy' or now >= limit:
                    job.pop('waiting', None)
                    self._end_job(lane, 'start refused (%s)' % res,
                                  stop=res)
                else:
                    job['t0'], job['deadline'] = now, limit
                continue
            idle = (fresh and lane < len(modes)
                    and modes[lane] in (0, 7)
                    and self._feed[0] >= job['t0'] + LOST_EVENT_S)
            if idle or now >= job['deadline']:
                if not idle:
                    # Keep ownership until an end event or fresh stopped report.
                    # Reuse the connection recovery path if STOP cannot settle.
                    job['stopping'] = True
                    self._follow_want.discard(lane)
                    self._stop_lane(lane)
                    if now >= job['deadline'] + 3.0:
                        raise LinkDead('feed STOP unconfirmed')
                    continue
                # The end event was lost: the lane encoder says how far
                # the piece ran. Without a reading nothing is booked, so
                # a retract reads short instead of done.
                ran = self._lost_piece_mm(lane, job) if idle else None
                if (idle and ran is None and job.get('enc0') is not None
                        and now < job.setdefault('lost_t', now)
                        + LOST_ENCODER_WAIT_S):
                    # The counters report after the idle one is still due.
                    continue
                if ran is not None:
                    job['done'] += ran
                self._end_job(lane, 'no end event (%s%s)'
                              % ('lane idle' if idle else 'deadline',
                                 '' if ran is None else ', encoder'),
                              stop='lost_event' if idle else 'timeout')
        self._relax_watch(now)
        self._follow_full_watch(now)
        self._lanes_tick(now)
        for lane in list(self._follow):
            if (fresh and lane < len(modes)
                    and modes[lane] not in FOLLOW_MODES
                    and self._feed[0] >= self._follow_t0.get(lane, 0.0)
                    + LOST_EVENT_S):
                # The follow ended without an event we saw: the lane would
                # starve the extruder (no freewheel). Armed again while the
                # host still wants it.
                self._follow.pop(lane, None)
                logging.warning(self._lp + 'follow on lane %d no '
                                'longer running (report mode %d)'
                                % (lane, modes[lane]))
                if lane in self._follow_want and lane not in self._jobs:
                    self._start_follow(lane)
                    self._note_follow_lost(lane, 'mode %d' % modes[lane])

    def _tag_tick(self):
        """End a read with motion whose terminal event was lost."""
        now = self._now()
        fresh = self._fresh(self._feed)
        modes = bytearray(self._feed[1].get('mode', b'')) if fresh else b''
        for lane in list(self._tag_reads):
            job = self._tag_reads[lane]
            idle = (fresh and lane < len(modes) and modes[lane] in (0, 7)
                    and self._feed[0] >= job['t0'] + LOST_EVENT_S)
            if idle or now >= job['deadline']:
                if not idle:
                    self._stop_lane(lane)
                    if now >= job['deadline'] + 3.0:
                        raise LinkDead('tag STOP unconfirmed')
                    continue
                self._end_tag_read(lane, 'lost_event')

    # -- follow instrument -------------------------------------------------
    #
    # The follow pushes on taut and pauses while the shared pulled reading
    # is set, whichever lane's plunger sets it (ace2k feed_assist.c). One
    # line per window answers whether the follow kept feeding while another
    # lane moved: the bursts per lane, the time the shared full was set,
    # the time each lane held it (its plunger neither at rest nor taut while
    # it was set), and the time a following lane stood taut under it.

    def _yield_auto(self, lane):
        """A host request on a lane the full-buffer watch is rolling
        back: the watch gives way."""
        job = self._jobs.get(lane)
        if job is None or not job.get('auto'):
            return
        job['stopping'] = True
        if self._stop_lane(lane):
            self._pump(STOP_EVENT_WAIT_S)
            self._work_events()
        # Leave an unsettled job owned by the lane; the existing tick
        # completes it from an event or fresh idle telemetry.

    def _motion(self, method, params, rid):
        def reply(code, msg='success'):
            return {'id': rid, 'code': code, 'msg': msg, 'result': {}}
        raw_lane = params.get('index', -1)
        lane = raw_lane if type(raw_lane) is int else -1
        if method in ('stop_feed_filament', 'stop_unwind_filament'):
            if 'index' in params and not 0 <= lane <= 3:
                return reply(FAILED, 'bad index')
            lanes = [lane] if 0 <= lane <= 3 else list(self._jobs)
            acknowledged = True
            for ln in lanes:
                job = self._jobs.get(ln)
                if job is not None:
                    job['stopping'] = True
                    if job.get('waiting'):
                        self._follow_want.discard(ln)
                        job['stopped'] = True
                        self._end_job(ln, 'cancelled before start', stop='stopped')
                # Feed STOP does not tear down an unrelated following mode.
                if job is not None or ln not in self._follow:
                    acknowledged = self._stop_lane(ln) and acknowledged
            if any(ln in self._jobs for ln in lanes):
                self._pump(STOP_EVENT_WAIT_S)
                self._work_events()
            return reply(0) if acknowledged else reply(FAILED, 'unanswered')
        if method in ('update_feeding_speed', 'update_unwinding_speed'):
            job = self._jobs.get(lane)
            if job is None or job.get('auto'):
                # The follow's speed is fixed by its own mode.
                return reply(0)
            if job.get('stopping'):
                return reply(1, 'FORBIDDEN')
            try:
                speed = self._speed_um(params.get('speed'))
            except (TypeError, ValueError, OverflowError):
                return reply(FAILED, 'bounds')
            if not job.get('waiting'):
                ans = self._call('ace2k_feed_speed_set', 'ace2k_feed_speed_set_response',
                                 lane=lane, speed_um_s=speed)
                if not ans or not ans.get('accepted') or ans.get('lane') != lane:
                    return reply(FAILED, 'speed not accepted')
            new_speed = speed / 1000.0
            if not job.get('waiting') and new_speed != job['speed']:
                floor = self.dictionary.constant('ACE2K_LANE_SPEED_MIN_UM_S',
                                                 int(SPEED_MIN_MM_S * 1000)) / 1000.0
                cap = job['t0'] + 1.5 * job['piece'] / floor + DEADLINE_EXTRA_S
                job['deadline'] = min(cap, self._now() + 1.5 * job['piece'] / new_speed + DEADLINE_EXTRA_S)
            job['speed'] = new_speed
            return reply(0)
        if method == 'stop_feed_assist':
            if 'index' in params and not 0 <= lane <= 3:
                return reply(FAILED, 'bad index')
            lanes = [lane] if 0 <= lane <= 3 else sorted(set(self._follow) | self._follow_want)
            acknowledged = True
            for ln in lanes:
                self._follow_want.discard(ln)
                self._follow_lost.pop(ln, None)
                acknowledged = self._stop_follow(ln) and acknowledged
            return reply(0) if acknowledged else reply(FAILED, 'unanswered')
        if not 0 <= lane <= 3:
            return reply(FAILED, 'bad index')
        self._yield_auto(lane)
        if method == 'start_feed_assist':
            self._follow_want.add(lane)
            if lane in self._follow:
                return reply(0)
            if lane in self._jobs:
                # Follows when the move ends.
                return reply(0)
            seq, why = self._start_follow(lane)
            if seq is None:
                if why == 'busy':
                    return reply(1, 'FORBIDDEN')
                self._follow_want.discard(lane)
                return reply(FAILED, why)
            return reply(0)
        if lane in self._jobs:
            return reply(1, 'FORBIDDEN')
        try:
            length = float(params.get('length', 0))
        except (TypeError, ValueError, OverflowError):
            length = 0.0
        try:
            speed = float(params.get('speed', 0) or 0)
        except (TypeError, ValueError, OverflowError):
            speed = float('nan')
        if not math.isfinite(length) or length <= 0 or not math.isfinite(speed):
            return reply(FAILED, 'bad length')
        kind = 'retract' if method == 'unwind_filament' else 'feed'
        if kind == 'retract':
            self._follow_want.discard(lane)
            self._follow_lost.pop(lane, None)
        if lane in self._follow:
            # Do not start a competing move if the follow STOP is unanswered.
            if not self._stop_follow(lane):
                return reply(1, 'FORBIDDEN')
        self._motion_seq += 1
        job = {'kind': kind, 'slot': lane, 'target': length, 'done': 0.0,
               'speed': self._speed_um(speed if speed > 0 else SPEED_MAX_MM_S) / 1000.0,
               'free_tip': kind == 'feed' and length > FREE_TIP_MIN_MM,
               'mseq': self._motion_seq}
        res = self._start_piece(job)
        if res != 'ok':
            job['stop'] = res
            self._last_motion = self._motion_of(job, False)
            self._lane_motion[lane] = self._last_motion
            logging.info(self._lp + '%s lane %d refused (%s)'
                         % (kind, lane, res))
            if res == 'busy' and self._lane_report():
                # The unit's own tie-break holds the lane: it goes back
                # now, the start is sent again until the lane is free.
                now = self._now()
                job.pop('stop', None)
                job.update(waiting=True, t0=now,
                           deadline=now + START_BUSY_RETRY_S)
                self._jobs[lane] = job
                return reply(0)
            if res == 'busy':
                return reply(1, 'FORBIDDEN')
            return reply(FAILED, res)
        self._jobs[lane] = job
        return reply(0)

    def _stop_lane(self, lane):
        return self._send('ace2k_feed_stop', lane=lane) is True

    def _stop_follow(self, lane):
        """Stop the running follow on the lane and work through its end
        event, so the lane is idle for the next start."""
        if lane not in self._follow:
            return True
        if not self._stop_lane(lane):
            return False
        self._pump(STOP_EVENT_WAIT_S)
        self._work_events()
        self._follow.pop(lane, None)
        return True

    def _execute(self, request):
        rid = request.get('id', 0)
        method = request.get('method', '')

        def ok(result):
            return {'id': rid, 'code': 0, 'msg': 'success', 'result': result}

        if method not in ('get_info', 'get_status', 'discover_device', 'get_lane_state', 'tag_read_motion',
                          'tag_read_load', 'relax_buffer', 'set_lanes',
                          'flash_led') + DRYER_METHODS + MOTION_METHODS:
            return {'id': rid, 'code': UNSUPPORTED,
                    'msg': 'unsupported:%s' % method, 'result': {}}
        if not self._ready:
            self._connect()
        # Keepalive + drain: one round trip every request (the heartbeat
        # asks once a second, well inside the unit's 3 s link window).
        self._call('get_clock', 'clock')
        self._pump()
        if self._shutdown is not None:
            text, self._shutdown = self._shutdown, None
            self._reset('the unit reported a shutdown (%s)' % text)
        # STOP takes precedence over queued busy retries in _tick.
        if method in ('stop_feed_filament', 'stop_unwind_filament', 'stop_feed_assist'):
            return self._motion(method, request.get('params') or {}, rid)
        try:
            self._tick()
            self._tag_tick()
            self._dryer_tick()
        except (IOError, OSError):
            raise
        except Exception:
            logging.exception(self._lp + 'motion step failed')
        if method == 'discover_device':
            ans = self._call('ace2k_uid_query', 'ace2k_uid_response')
            raw = (ans or {}).get('uid', b'')
            words = chip_words(raw)
            if not words:
                self.uid = b''
                return None
            self.uid = bytes(raw)
            return ok({'uid1': words[0], 'uid2': words[1], 'uid3': words[2],
                       'firmware': 'ace2k %s' % (self.version or '?')})
        if method == 'get_lane_state':
            slot = (request.get('params') or {}).get('index')
            sample = native_sample(self._evidence_sensors, self._feed,
                                   self._evidence_enc, self._fg, slot, self._now())
            if sample is None:
                return None
            # Keep pending host work distinct from actual idle telemetry.
            state = sample.state
            if state == 'ready' and slot in self._jobs:
                state = ('rollback' if self._jobs[slot]['kind'] == 'retract' else 'feeding')
            if state == 'ready' and slot in self._tag_reads:
                state = 'preloading'
            if state == 'ready' and (slot in self._follow or slot in self._follow_want):
                state = 'assisting'
            return ok({'index': slot, 'present': sample.present, 'state': state,
                       'encoder': sample.encoder, 'motor': sample.motor,
                       'stamp': list(sample.stamp)})
        if method == 'tag_read_motion':
            return self._tag_read_motion(request.get('params') or {}, rid)
        if method == 'tag_read_load':
            return self._tag_read_load(request.get('params') or {}, rid)
        if method == 'relax_buffer':
            return self._relax_buffer(request.get('params') or {}, rid)
        if method == 'set_lanes':
            return ok(self._set_lanes(request.get('params') or {}))
        if method == 'flash_led':
            return self._identify(request.get('params') or {}, rid)
        if method in DRYER_METHODS:
            return self._dryer_request(method, request.get('params') or {},
                                       rid)
        if method == 'get_info':
            return ok({'model': MODEL,
                       'firmware': 'ace2k %s' % (self.version or '?')})
        if method == 'get_status':
            return self._status(ok)
        return self._motion(method, request.get('params') or {}, rid)

    def _on_event(self, ev):
        lane = int(ev.get('lane', 255))
        kind = int(ev.get('kind', -1))
        name = KIND_NAMES.get(kind, 'kind_%d' % kind)
        seq = int(ev.get('seq', 0))
        moved = abs(int(ev.get('filament_um', 0))) / 1000.0
        if kind in NOTICE_KINDS:
            logging.info(self._lp + 'lane %d notice %s' % (lane, name))
            return
        read = self._tag_reads.get(lane)
        if read is not None and (
                (seq == 0 and int(ev.get('mode', -1)) == 4)
                or (seq and read.get('seq') == seq)):
            # The terminal event of the unit's load that carried the read:
            # the read with motion (seq 0, mode loading) or the load we
            # started on an insert (its own seq).
            self._end_tag_read(lane, name, moved)
            return
        if self._follow.get(lane) == seq and seq:
            # The follow ended.
            self._follow.pop(lane, None)
            if kind in ERROR_KINDS:
                self._clear_lane(lane, 'follow %s' % name)
            if name != 'stopped':
                logging.warning(self._lp + 'follow on lane %d ended '
                                '(%s)' % (lane, name))
            if name == 'stopped_link' and lane in self._follow_want:
                self._start_follow(lane)
            if name != 'stopped':
                self._note_follow_lost(lane, name)
            return
        job = self._jobs.get(lane)
        if job is None or job.get('seq') != seq or not seq:
            return
        job['done'] += moved
        if name == 'done' and not job.get('stopping'):
            if job['target'] - job['done'] > REMAIN_MIN_MM:
                if self._now() >= job['deadline']:
                    self._follow_want.discard(lane)
                    self._end_job(lane, 'deadline', stop='timeout')
                    return
                res = self._start_piece(job)
                if res != 'ok':
                    self._end_job(lane, 'next piece refused (%s)' % res,
                                  stop=res)
                return
            self._end_job(lane, 'done', stop='distance')
            return
        if kind in ERROR_KINDS or name == 'blocked':
            # Where the strand was held: taut = toward the head, full = slack
            # in the unit (an obstacle ahead), at rest = nothing pulling.
            logging.info(self._lp + 'lane %d %s %s: %s'
                         % (lane, job['kind'], name,
                            self._lane_buffer_text(lane)))
        if kind in ERROR_KINDS:
            self._clear_lane(lane, name)
        stop = name
        if name == 'stopped' or name in RELAX_END_KINDS:
            job['stopped'] = True
        elif job['kind'] == 'feed' and (
                kind in ERROR_KINDS or name == 'blocked') \
                and moved < SLIP_MOVED_MM:
            # The filament did not come out of the unit: slip / tangle at
            # the MMU (the load-fail wording reads 'slip').
            stop = 'slip'
        self._end_job(lane, name, stop=stop)
