#ifndef HV_WEBSOCKET_CLIENT_H_
#define HV_WEBSOCKET_CLIENT_H_

/*
 * @demo examples/websocket_client_test.cpp
 */

#include "hexport.h"

#include "TcpClient.h"
#include "WebSocketChannel.h"

#include "HttpParser.h"
#include "WebSocketParser.h"

#include <atomic>

namespace hv {

class HV_EXPORT WebSocketClient : public TcpClientTmpl<WebSocketChannel> {
public:
    std::string           url;
    std::function<void()> onopen;
    std::function<void()> onclose;
    std::function<void(const std::string& msg)> onmessage;
    // PATCH: onmessage not given opcode
    enum ws_opcode opcode() { return channel ? channel->opcode : WS_OPCODE_CLOSE; }

    WebSocketClient(EventLoopPtr loop = NULL);
    virtual ~WebSocketClient();

    // url = ws://ip:port/path
    // url = wss://ip:port/path
    int open(const char* url, const http_headers& headers = DefaultHeaders);
    int close();
    int send(const std::string& msg);
    int send(const char* buf, int len, enum ws_opcode opcode = WS_OPCODE_BINARY);

    // setConnectTimeout / setPingInterval / setReconnect
    void setPingInterval(int ms) {
        ping_interval = ms;
    }

    // NOTE: call before open
    void setHttpRequest(const HttpRequestPtr& req) {
        http_req_ = req;
    }

    // NOTE: call when onopen
    const HttpResponsePtr& getHttpResponse() {
        return http_resp_;
    }

private:
    enum State {
        CONNECTING,
        CONNECTED,
        WS_UPGRADING,
        WS_OPENED,
        WS_CLOSED,
    };
    // PATCH: atomic because close() writes it from whatever thread calls
    // disconnect() while the event-loop thread reads it in the onmessage
    // handler (`if (state == WS_OPENED ...)`). Plain enum access there is a
    // data race TSAN reports on every nightly run, and on a weakly ordered
    // target the reader can observe a stale state across the close.
    std::atomic<State>  state;
    HttpParserPtr       http_parser_;
    HttpRequestPtr      http_req_;
    HttpResponsePtr     http_resp_;
    WebSocketParserPtr  ws_parser_;
    // ping/pong
    int                 ping_interval;
    int                 ping_cnt;
    // PATCH: reconnect backoff snapshot taken at TCP connect and restored on close
    // unless the WS upgrade completed, so a peer that accepts TCP but fails the
    // upgrade does not reset the backoff to min_delay on every cycle.
    uint32_t            saved_reconn_cur_delay_;
    uint32_t            saved_reconn_cur_retry_cnt_;
    bool                saved_reconn_valid_;
};

}

#endif // HV_WEBSOCKET_CLIENT_H_
