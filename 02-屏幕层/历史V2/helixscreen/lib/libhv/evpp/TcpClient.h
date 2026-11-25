#ifndef HV_TCP_CLIENT_HPP_
#define HV_TCP_CLIENT_HPP_

#include <atomic>
#include <mutex>

#include "hsocket.h"
#include "hssl.h"
#include "hlog.h"

#include "EventLoopThread.h"
#include "Channel.h"

namespace hv {

template<class TSocketChannel = SocketChannel>
class TcpClientEventLoopTmpl {
public:
    typedef std::shared_ptr<TSocketChannel> TSocketChannelPtr;

    TcpClientEventLoopTmpl(EventLoopPtr loop = NULL) {
        loop_ = loop ? loop : std::make_shared<EventLoop>();
        reconn_timer_id = INVALID_TIMER_ID;
        remote_port = 0;
        connect_timeout = HIO_DEFAULT_CONNECT_TIMEOUT;
        tls = false;
        tls_setting = NULL;
        reconn_setting = NULL;
        unpack_setting = NULL;
    }

    virtual ~TcpClientEventLoopTmpl() {
        {
            std::lock_guard<std::mutex> lock(reconn_mtx);
            HV_FREE(reconn_setting);
        }
        HV_FREE(tls_setting);
        HV_FREE(unpack_setting);
    }

    const EventLoopPtr& loop() {
        return loop_;
    }

    // delete thread-safe
    void deleteInLoop() {
        loop_->runInLoop([this](){
            delete this;
        });
    }

    // NOTE: By default, not bind local port. If necessary, you can call bind() after createsocket().
    // @retval >=0 connfd, <0 error
    int createsocket(int remote_port, const char* remote_host = "127.0.0.1") {
        memset(&remote_addr, 0, sizeof(remote_addr));
        int ret = sockaddr_set_ipport(&remote_addr, remote_host, remote_port);
        if (ret != 0) {
            return NABS(ret);
        }
        this->remote_host = remote_host;
        this->remote_port = remote_port;
        return createsocket(&remote_addr.sa);
    }

    int createsocket(struct sockaddr* remote_addr) {
        // hv::EventLoop::stop() nulls its hloop_t* on the CALLING thread while
        // hloop_run() is still executing on the loop thread. A callback that
        // lands in that window — typically the auto-reconnect timer armed by
        // startReconnect() — would hand NULL to hio_get() and fault reading
        // loop->ios. EventLoop::setTimer() and postEvent() already bail on a
        // null loop_; this call site was the one that did not.
        hloop_t* hl = loop_->loop();
        if (hl == NULL) {
            return -3;
        }

        int connfd = ::socket(remote_addr->sa_family, SOCK_STREAM, 0);
        // SOCKADDR_PRINT(remote_addr);
        if (connfd < 0) {
            perror("socket");
            return -2;
        }
        hv_run_outbound_socket_hook(connfd, remote_addr);

        hio_t* io = hio_get(hl, connfd);
        assert(io != NULL);
        hio_set_peeraddr(io, remote_addr, SOCKADDR_LEN(remote_addr));
        // Hand the previous channel to the loop thread to destroy.
        //
        // This runs on the CALLER's thread (connect(), or a reconnect retry)
        // while the loop thread may be inside that same channel's onclose:
        // hio_close() invokes a std::function that LIVES IN the channel object,
        // so dropping the last reference here frees the functor out from under
        // the call. ASan caught it as an 8-byte read of the captured `this` in
        // startConnect()'s onclose lambda, reached via __connect_timeout_cb ->
        // hio_close (nightly, test_moonraker_connection_retry). runInLoop() is
        // queueInLoop, NOT runInLoop: the dangerous case is re-entrant, and
        // runInLoop() executes inline when it is already on the loop thread
        // (`if (isRunning() && isInLoopThread()) fn();`), which is precisely when
        // this happens. onclose calls back into user code synchronously, that
        // code reconnects, and connect() lands here with the old channel's
        // onclose still on the stack:
        //
        //   hio_close -> channel A onclose -> MoonrakerClient::on_ws_close ->
        //   connect() -> WebSocketClient::open -> createsocket -> frees A
        //
        // and then control returns into A's onclose, which reads its captured
        // `this` out of freed storage. queueInLoop always defers to the next
        // loop iteration, so the release cannot land before the stack unwinds.
        // Same caller-thread-versus-loop-thread family as reconn_setting above,
        // different member.
        TSocketChannelPtr prev;
        {
            std::lock_guard<std::mutex> lock(channel_mtx);
            prev = channel;
            channel = std::make_shared<TSocketChannel>(io);
        }
        if (prev) {
            loop_->queueInLoop([prev]() mutable { prev.reset(); });
        }
        return connfd;
    }

    int bind(int local_port, const char* local_host = "0.0.0.0") {
        sockaddr_u local_addr;
        memset(&local_addr, 0, sizeof(local_addr));
        int ret = sockaddr_set_ipport(&local_addr, local_host, local_port);
        if (ret != 0) {
            return NABS(ret);
        }
        return bind(&local_addr.sa);
    }

    int bind(struct sockaddr* local_addr) {
        TSocketChannelPtr ch = channel_ref();
        if (ch == NULL || ch->isClosed()) {
            return -1;
        }
        int ret = ::bind(ch->fd(), local_addr, SOCKADDR_LEN(local_addr));
        if (ret != 0) {
            perror("bind");
        }
        return ret;
    }

    // closesocket thread-safe
    void closesocket() {
        TSocketChannelPtr ch = channel_ref();
        if (ch && ch->status != SocketChannel::CLOSED) {
            loop_->runInLoop([this](){
                // Re-read on the loop thread rather than capturing the copy
                // above: this closes whichever channel is current when the
                // post runs, which is what the caller asked for.
                TSocketChannelPtr ch = channel_ref();
                if (ch) {
                    setReconnect(NULL);
                    ch->close();
                }
            });
        }
    }

    // Re-resolve remote_host into remote_addr. open()/createsocket(port,host)
    // resolves exactly once, and every auto-reconnect retry dials via
    // createsocket(sockaddr*) — the overload that never resolves — so a
    // hostname whose IP changed during a network blip (router reboot
    // re-lease, mesh roaming, mDNS re-announce) was retried at its stale
    // address forever. Literal IPs short-circuit inside sockaddr_set_ipport
    // and pay no DNS. On resolution failure the last known address is KEPT:
    // the retry chain stays armed and dials something, and the next retry
    // re-resolves — zeroing it out or killing the chain would trade a stale
    // dial for no dial at all.
    bool refresh_remote_addr() {
        if (remote_host.empty()) return false;
        sockaddr_u addr;
        memset(&addr, 0, sizeof(addr));
        if (sockaddr_set_ipport(&addr, remote_host.c_str(), remote_port) != 0) {
            hlogw("re-resolve %s:%d failed, keep last known address\n",
                  remote_host.c_str(), remote_port);
            return false;
        }
        memcpy(&remote_addr, &addr, sizeof(addr));
        return true;
    }

    int startConnect() {
        if (channel == NULL || channel->isClosed()) {
            // Re-resolve on every dial attempt — the retry path must not be
            // pinned to the address that was current at open() time.
            refresh_remote_addr();
            int connfd = createsocket(&remote_addr.sa);
                if (connfd < 0) {
                    hloge("createsocket %s:%d return %d!\n", remote_host.c_str(), remote_port, connfd);
                    // Reschedule instead of dropping the chain. Only channel->onclose
                    // re-arms the retry timer, and it is installed further down this
                    // function, so returning here leaves nothing to restart the client:
                    // one transient ::socket() failure (EMFILE/ENFILE, ENOBUFS under
                    // memory pressure) killed auto-reconnect permanently. Respects
                    // max_retry_cnt via reconn_setting_can_retry(). -3 is the
                    // exception: the event loop is gone, so there is nothing left to
                    // retry on and re-arming would only churn during shutdown.
                    bool retry;
                    {
                        std::lock_guard<std::mutex> lock(reconn_mtx);
                        retry = reconn_setting && connfd != -3;
                    }
                    if (retry) {
                        startReconnect();
                    }
                    return connfd;
                }
        }
        if (channel == NULL || channel->status >= SocketChannel::CONNECTING) {
            return -1;
        }
        if (connect_timeout) {
            channel->setConnectTimeout(connect_timeout);
        }
        if (tls) {
            channel->enableSSL();
            if (tls_setting) {
                int ret = channel->newSslCtx(tls_setting);
                if (ret != 0) {
                    hloge("new SSL_CTX failed: %d", ret);
                    closesocket();
                    return ret;
                }
            }
            if (!is_ipaddr(remote_host.c_str())) {
                channel->setHostname(remote_host);
            }
        }
        channel->onconnect = [this]() {
            if (unpack_setting) {
                channel->setUnpack(unpack_setting);
            }
            channel->startRead();
            if (onConnection) {
                onConnection(channel);
            }
            std::lock_guard<std::mutex> lock(reconn_mtx);
            if (reconn_setting) {
                reconn_setting_reset(reconn_setting);
            }
        };
        channel->onread = [this](Buffer* buf) {
            if (onMessage) {
                onMessage(channel, buf);
            }
        };
        channel->onwrite = [this](Buffer* buf) {
            if (onWriteComplete) {
                onWriteComplete(channel, buf);
            }
        };
        channel->onclose = [this]() {
            bool reconnect;
            {
                std::lock_guard<std::mutex> lock(reconn_mtx);
                reconnect = reconn_setting != NULL;
            }
            if (onConnection) {
                onConnection(channel);
            }
            if (reconnect) {
                startReconnect();
            }
        };
        return channel->startConnect();
    }

    int startReconnect() {
        std::lock_guard<std::mutex> lock(reconn_mtx);
        if (!reconn_setting) return -1;
        if (!reconn_setting_can_retry(reconn_setting)) return -2;
        uint32_t delay = reconn_setting_calc_delay(reconn_setting);
        hlogi("reconnect... cnt=%d, delay=%d", reconn_setting->cur_retry_cnt, reconn_setting->cur_delay);
        reconn_timer_id = loop_->setTimeout(delay, [this](TimerID timerID){
            (void)(timerID);
            reconn_timer_id = INVALID_TIMER_ID;
            // setReconnect(NULL) ran after this retry was armed — the client is
            // disconnecting or being destroyed. Reconnecting now resurrects a
            // socket on a torn-down client and, once the loop starts stopping,
            // faults in createsocket().
            bool armed;
            {
                std::lock_guard<std::mutex> lock(reconn_mtx);
                armed = reconn_setting != NULL;
            }
            if (!armed) return;
            startConnect();
        });
        return 0;
    }

    // start thread-safe
    void start() {
        loop_->runInLoop(std::bind(&TcpClientEventLoopTmpl::startConnect, this));
    }

    bool isConnected() {
        TSocketChannelPtr ch = channel_ref();
        if (ch == NULL) return false;
        return ch->isConnected();
    }

    // send thread-safe
    int send(const void* data, int size) {
        // One snapshot for both the check and the write. Reading the member
        // twice let a reconnect swap the channel in between, so the write
        // could land on a different object than the one just tested.
        TSocketChannelPtr ch = channel_ref();
        if (ch == NULL || !ch->isConnected()) return -1;
        return ch->write(data, size);
    }
    int send(Buffer* buf) {
        return send(buf->data(), buf->size());
    }
    int send(const std::string& str) {
        return send(str.data(), str.size());
    }

    int withTLS(hssl_ctx_opt_t* opt = NULL) {
        tls = true;
        if (opt) {
            if (tls_setting == NULL) {
                HV_ALLOC_SIZEOF(tls_setting);
            }
            opt->endpoint = HSSL_CLIENT;
            *tls_setting = *opt;
        }
        return 0;
    }

    void setConnectTimeout(int ms) {
        connect_timeout = ms;
    }

    void setReconnect(reconn_setting_t* setting) {
        std::lock_guard<std::mutex> lock(reconn_mtx);
        if (setting == NULL) {
            // Cancel a retry already armed by startReconnect(). Upstream never
            // stored the TimerID, so disabling reconnect could not stop a timer
            // that was already ticking. Only effective when called from the loop
            // thread (killTimer defers otherwise) — the reconn_setting re-check
            // in startReconnect()'s callback covers the rest.
            TimerID armed = reconn_timer_id.exchange(INVALID_TIMER_ID);
            if (armed != INVALID_TIMER_ID) {
                loop_->killTimer(armed);
            }
            HV_FREE(reconn_setting);
            return;
        }
        if (reconn_setting == NULL) {
            HV_ALLOC_SIZEOF(reconn_setting);
        }
        *reconn_setting = *setting;
    }
    bool isReconnect() {
        std::lock_guard<std::mutex> lock(reconn_mtx);
        return reconn_setting && reconn_setting->cur_retry_cnt > 0;
    }

    void setUnpack(unpack_setting_t* setting) {
        if (setting == NULL) {
            HV_FREE(unpack_setting);
            return;
        }
        if (unpack_setting == NULL) {
            HV_ALLOC_SIZEOF(unpack_setting);
        }
        *unpack_setting = *setting;
    }

public:
    TSocketChannelPtr       channel;

    // channel is assigned by createsocket() and read by callers on any thread
    // (closesocket/isConnected/bind/send), while the reconnect timer reaches
    // createsocket() from the loop thread through startConnect(). A shared_ptr
    // is two words and copying one is not atomic, so an unsynchronised read
    // taken during a reassignment yields a torn pointer and the next virtual
    // call on it branches into whatever now occupies that memory.
    //
    // Every access takes a COPY under this mutex and then works from the copy.
    // The copy is what makes this safe rather than merely narrower: it holds a
    // reference, so the object stays alive for the duration of the call even
    // if the loop thread replaces the member the instant the lock is released.
    // Never hold it across a call that re-enters this class - closesocket()
    // posts to the loop, and startConnect()/setReconnect() take reconn_mtx
    // themselves.
    //
    // startConnect() and the channel callbacks it installs still read the
    // member directly. They run on the loop thread, which is also where the
    // reconnect path writes it, so they do not cross the boundary this guards.
    mutable std::mutex      channel_mtx;

    // A counted reference to the current channel, or NULL.
    TSocketChannelPtr channel_ref() const {
        std::lock_guard<std::mutex> lock(channel_mtx);
        return channel;
    }

    std::string             remote_host;
    int                     remote_port;
    sockaddr_u              remote_addr;
    int                     connect_timeout;
    bool                    tls;
    hssl_ctx_opt_t*         tls_setting;
    reconn_setting_t*       reconn_setting;
    unpack_setting_t*       unpack_setting;
    // TimerID of the retry armed by startReconnect(), so setReconnect(NULL) can
    // cancel it. Written from the loop thread (the timer callback) and the
    // caller's thread (setReconnect), hence atomic.
    std::atomic<TimerID>    reconn_timer_id;

    // reconn_setting is touched from the caller's thread (setReconnect during
    // connect) AND the event loop thread (closesocket's runInLoop lambda calls
    // setReconnect(NULL) -> HV_FREE; the reconnect timer dereferences the
    // struct). Upstream assumed single-threaded use, so a connect() racing a
    // close() was a use-after-free on the struct and the pointer (helixscreen
    // nightly, test_moonraker_client_robustness concurrent-connect). Every
    // access below takes this mutex; never hold it across a call that re-enters
    // this class (startConnect/closesocket take it themselves).
    std::mutex              reconn_mtx;

    // Callback
    std::function<void(const TSocketChannelPtr&)>           onConnection;
    std::function<void(const TSocketChannelPtr&, Buffer*)>  onMessage;
    // NOTE: Use Channel::isWriteComplete in onWriteComplete callback to determine whether all data has been written.
    std::function<void(const TSocketChannelPtr&, Buffer*)>  onWriteComplete;

private:
    EventLoopPtr            loop_;
};

template<class TSocketChannel = SocketChannel>
class TcpClientTmpl : private EventLoopThread, public TcpClientEventLoopTmpl<TSocketChannel> {
public:
    TcpClientTmpl(EventLoopPtr loop = NULL)
        : EventLoopThread(loop)
        , TcpClientEventLoopTmpl<TSocketChannel>(EventLoopThread::loop())
        , is_loop_owner(loop == NULL)
    {}
    virtual ~TcpClientTmpl() {
        stop(true);
    }

    const EventLoopPtr& loop() {
        return EventLoopThread::loop();
    }

    // start thread-safe
    void start(bool wait_threads_started = true) {
        if (isRunning()) {
            TcpClientEventLoopTmpl<TSocketChannel>::start();
        } else {
            EventLoopThread::start(wait_threads_started, [this]() {
                TcpClientTmpl::startConnect();
                return 0;
            });
        }
    }

    // stop thread-safe
    void stop(bool wait_threads_stopped = true) {
        TcpClientEventLoopTmpl<TSocketChannel>::closesocket();
        if (is_loop_owner) {
            EventLoopThread::stop(wait_threads_stopped);
        }
    }

private:
    bool is_loop_owner;
};

typedef TcpClientTmpl<SocketChannel> TcpClient;

}

#endif // HV_TCP_CLIENT_HPP_
