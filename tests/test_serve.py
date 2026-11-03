import socket
import threading
import urllib.request

from barrierfrei.serve import serve, start


def test_start_serves_the_directory(tmp_path):
    (tmp_path / "plan.html").write_text("hello map", encoding="utf-8")
    server, url = start(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(url + "plan.html") as resp:
            assert resp.status == 200
            assert resp.read().decode() == "hello map"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_start_binds_localhost_only(tmp_path):
    server, _url = start(tmp_path, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_start_falls_back_when_port_taken(tmp_path):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        taken = blocker.getsockname()[1]
        server, url = start(tmp_path, port=taken)
        try:
            assert server.server_address[1] != taken
            assert url == f"http://127.0.0.1:{server.server_address[1]}/"
        finally:
            server.server_close()
    finally:
        blocker.close()


def test_serve_does_not_open_browser_when_asked(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("webbrowser.open", lambda url: calls.append(url))

    def _stop(self):
        raise KeyboardInterrupt

    monkeypatch.setattr("http.server.ThreadingHTTPServer.serve_forever", _stop)

    serve(tmp_path, open_browser=False)
    assert calls == []

    serve(tmp_path, open_browser=True)
    assert len(calls) == 1
    assert calls[0].endswith("/plan.html")
