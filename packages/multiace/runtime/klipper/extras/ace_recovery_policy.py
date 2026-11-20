"""Finite recovery budget; no wire commands or physical-state decisions here."""


class RecoveryBudget:
    def __init__(self, limit=2, retry_delay=1.5, healthy_delay=2.0):
        self.limit = limit
        self.retry_delay = retry_delay
        self.healthy_delay = healthy_delay
        self.episodes = {}

    def observe(self, key, context, needed, now):
        item = self.episodes.get(key)
        if not needed:
            if item is not None:
                if item['healthy_since'] is None:
                    item['healthy_since'] = now
                elif now - item['healthy_since'] >= self.healthy_delay:
                    del self.episodes[key]
            return False
        if item is None:
            item = self.episodes[key] = {
                'context': context, 'attempts': 0, 'state': 'pending',
                'next_at': now, 'message': '等待设备空闲后自动检查',
                'healthy_since': None, 'created_at': now,
            }
        item['healthy_since'] = None
        if item['context'] is None and item['attempts'] == 0 and item['state'] == 'pending':
            # No device was authorized while its identity was unavailable.
            # The controller validates fault/source identity before it starts.
            item['context'] = context
        if item['context'] != context:
            item.update(state='blocked', message='设备连接或料路来源已变化，请重新检查')
            return False
        return (item['state'] in ('pending', 'retry_wait')
                and item['attempts'] < self.limit and now >= item['next_at'])

    def start(self, key):
        item = self.episodes[key]
        item.update(attempts=item['attempts'] + 1, state='running',
                    message='正在自动检查停止状态')

    def finish(self, key, now, stopped, message, needs_attention=False, retry=True):
        item = self.episodes.get(key)
        if item is None:
            return
        if stopped:
            state = 'needs_attention' if needs_attention else 'recovered'
        elif retry and item['attempts'] < self.limit:
            state = 'retry_wait'
        else:
            state = 'needs_attention'
        item.update(state=state, next_at=now + self.retry_delay, message=message)

    def block(self, key, message):
        item = self.episodes.get(key)
        if item is not None:
            item.update(state='blocked', message=message)

    def defer(self, key, now, message):
        """A temporarily missing sensor is not a failed wire attempt."""
        item = self.episodes.get(key)
        if item is not None:
            item.update(state='pending' if now - item['created_at'] < 30.0 else 'needs_attention',
                        next_at=now + self.retry_delay, message=message)

    def snapshot(self, key):
        item = self.episodes.get(key)
        if item is None:
            return {'state': 'idle', 'attempts': 0, 'limit': self.limit, 'message': ''}
        result = {name: item[name] for name in ('state', 'attempts', 'message')}
        result['limit'] = self.limit
        return result
