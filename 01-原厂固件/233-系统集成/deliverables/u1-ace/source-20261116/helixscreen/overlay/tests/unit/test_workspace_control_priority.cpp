// SPDX-License-Identifier: GPL-3.0-or-later
#include "../helix_test_fixture.h"
#include "../test_helpers/scoped_runtime_config.h"
#include "../catch_amalgamated.hpp"
#include "core/http_executor.h"
#include "core/update_queue.h"
#include "http_listen_socket.h"
#include "material_workspace.h"
#include "hv/HttpServer.h"
#include "hv/EventLoop.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <future>
#include <mutex>
#include <thread>
#include <unistd.h>

TEST_CASE_METHOD(HelixTestFixture,
                 "operator control bypasses four blocked workspace HTTP requests",
                 "[material-workspace][workspace-control][serial]") {
    using namespace std::chrono_literals;
    ScopedRuntimeConfig runtime;
    get_runtime_config()->test_mode = false;
    helix::http::HttpExecutor::start_all();
    std::mutex mutex;
    std::condition_variable condition;
    int normal_arrived = 0, control_arrived = 0;
    std::atomic<bool> release_normal{false};
    hv::HttpService service;
    service.GET("/state", [&](const HttpContextPtr& context) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            ++normal_arrived;
        }
        condition.notify_all();
        hv::setInterval(5, [context, &release_normal](hv::TimerID timer) {
            if (!release_normal.load()) return;
            context->send("{\"ok\":true}");
            hv::killTimer(timer);
        });
        return HTTP_STATUS_UNFINISHED;
    });
    for (const char* path : {"/path-calibration/stop", "/path-calibration/cancel"}) {
        service.POST(path, [&](HttpRequest*, HttpResponse* response) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                ++control_arrived;
            }
            condition.notify_all();
            response->content_type = APPLICATION_JSON;
            response->body = "{\"ok\":true}";
            return 200;
        });
    }
    int port = 0;
    const int descriptor = helix::bind_tcp_listener("127.0.0.1", 7136, &port);
    REQUIRE(descriptor >= 0);
    hv::HttpServer server;
    server.registerHttpService(&service);
    server.setListenFD(descriptor);
    server.setPort(0);
    server.setThreadNum(6);
    const int started = server.start();
    if (started != 0) ::close(descriptor);
    REQUIRE(started == 0);
    struct Cleanup {
        std::atomic<bool>& pending;
        hv::HttpServer& server;
        void release() { pending.store(true); }
        ~Cleanup() { release(); server.stop(); }
    } cleanup{release_normal, server};
    struct Replies {
        std::atomic<int> normal{0}, control{0};
        std::atomic<bool> failed{false};
    };
    auto replies = std::make_shared<Replies>();
    for (int index = 0; index < 4; ++index)
        helix::material_workspace::request("/state", {}, [replies](auto, const std::string& error) {
            if (!error.empty()) replies->failed.store(true);
            ++replies->normal;
        });
    {
        std::unique_lock<std::mutex> lock(mutex);
        const bool ready = condition.wait_for(lock, 2s, [&] { return normal_arrived == 4; });
        INFO("normal arrivals=" << normal_arrived << " fast inflight=" << helix::http::HttpExecutor::fast().inflight());
        REQUIRE(ready);
    }
    for (const char* path : {"/path-calibration/stop", "/path-calibration/cancel"})
        helix::material_workspace::request(path, {{"task_id", "test-task"}},
            [replies](auto, const std::string& error) {
                if (!error.empty()) replies->failed.store(true);
                ++replies->control;
            }, true);
    {
        std::unique_lock<std::mutex> lock(mutex);
        CHECK(condition.wait_for(lock, 1s, [&] { return control_arrived == 2; }));
        CHECK(normal_arrived == 4);
    }
    cleanup.release();
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while ((helix::http::HttpExecutor::fast().inflight() ||
            helix::http::HttpExecutor::control().inflight()) &&
           std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(2ms);
    REQUIRE(helix::http::HttpExecutor::fast().inflight() == 0);
    REQUIRE(helix::http::HttpExecutor::control().inflight() == 0);
    helix::ui::UpdateQueue::instance().drain_until_empty();
    CHECK_FALSE(replies->failed.load());
    CHECK(replies->normal.load() == 4);
    CHECK(replies->control.load() == 2);
}
