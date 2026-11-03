"""Published build routes and tokens, on loopback with invented IPA bytes."""
import http.client
import json
import plistlib
import threading
from http.server import ThreadingHTTPServer

import pytest

import ota
import serve
from runtime import Refused, write_json

TOKEN = "synthetic-install-token-1234567890"


def test_token_publish_and_manifest(tmp_path):
    token_path = tmp_path / '.private/ota_token'
    token = ota.load_or_create_token(token_path)
    assert token == ota.load_or_create_token(token_path)
    assert token_path.stat().st_mode & 0o777 == 0o600
    ipa = tmp_path / 'input.ipa'
    ipa.write_bytes(b'PK synthetic IPA')
    directory = tmp_path / 'data/ota'
    inf = ota.publish(ipa, '1.0', '7', 'org.example.careerapp', directory)
    assert ota.info(directory)['build'] == '7'
    item = plistlib.loads(ota.manifest(inf, TOKEN, 'https://deploy.example.org'))['items'][0]
    assert item['metadata']['bundle-identifier'] == 'org.example.careerapp'
    assert item['assets'][0]['url'] == f'https://deploy.example.org/app/install/{TOKEN}/Career.ipa'
    assert b'itms-services:' in ota.page(inf, TOKEN, 'https://deploy.example.org')
    with pytest.raises(Refused):
        ota.manifest(inf, TOKEN, 'http://deploy.example.org')


@pytest.mark.parametrize('handler', [serve.Handler, serve.LinkPageHandler])
def test_install_token_is_scoped_and_never_logged(handler, tmp_path, monkeypatch, capsys):
    ipa = tmp_path / 'input.ipa'
    ipa.write_bytes(b'PK synthetic IPA')
    ota.publish(ipa, '1.0', '7', 'org.example.careerapp', tmp_path / 'data/ota')
    (tmp_path / '.private').mkdir()
    (tmp_path / '.private/ota_token').write_text(TOKEN)
    write_json(tmp_path / 'settings.json', {'ota_base': 'https://deploy.example.org'})
    monkeypatch.setattr(handler, 'files_root', tmp_path, raising=False)
    monkeypatch.setattr(handler, 'token', 'synthetic-api-token', raising=False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def get(path, method='GET'):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port)
        client.request(method, path)
        response = client.getresponse()
        result = response.status, response.read()
        client.close()
        return result
    try:
        base = '/app/install/' + TOKEN + '/'
        assert get(base)[0] == 200
        assert get(base + 'manifest.plist')[0] == 200
        assert get(base + 'Career.ipa') == (200, b'PK synthetic IPA')
        assert get(base + 'Career.ipa', 'HEAD') == (200, b'')
        for name in ('info.json', '../settings.json', 'extra/Career.ipa'):
            assert get(base + name)[0] == 404
        assert get('/app/install/wrong/')[0] == 404
        assert get('/api/health?token=' + TOKEN)[0] == 401
        assert len(handler._auth_fails['127.0.0.1']) == 5
        assert TOKEN not in capsys.readouterr().err
        monkeypatch.setattr(serve, 'AUTH_FAIL_LIMIT', 1)
        assert get(base)[0] == 429
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
