"""Real build orchestration with fake Xcode commands and a scratch export."""
from pathlib import Path
import plistlib
import subprocess

import pytest

from build_app import build
import ota
from runtime import read_json, write_json
from setup import prepare


def test_archive_export_publish_uses_workspace_signing_and_reserves_builds(tmp_path):
    prepare(tmp_path, platform='darwin')
    write_json(tmp_path / 'settings.json', {'apple_team_id':'SYNTHETIC', 'bundle_id_prefix':'org.example',
                                           'ota_base':'https://deploy.example.org'})
    calls = []
    def xcode(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs['env']['APPLE_TEAM_ID'] == 'SYNTHETIC'
        assert kwargs['env']['BUNDLE_ID_PREFIX'] == 'org.example'
        assert kwargs['cwd'] == tmp_path / 'app'
        if 'archive' in argv:
            assert 'CURRENT_PROJECT_VERSION=1' in argv
            archive = Path(argv[argv.index('-archivePath') + 1])
            app = archive / 'Products/Applications/Career.app'
            app.mkdir(parents=True)
            (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.example.careerapp',
                                                            'CFBundleShortVersionString':'1.0'}))
        elif '-exportArchive' in argv:
            options = plistlib.loads(Path(argv[argv.index('-exportOptionsPlist') + 1]).read_bytes())
            assert options['teamID'] == 'SYNTHETIC' and options['method'] == 'debugging'
            export = Path(argv[argv.index('-exportPath') + 1])
            export.mkdir()
            (export / 'Career.ipa').write_bytes(b'PK synthetic export')
        return subprocess.CompletedProcess(argv, 0)
    result = build(tmp_path, run=xcode)
    assert result['build']['build'] == '1'
    assert result['url'].startswith('https://deploy.example.org/app/install/')
    assert (tmp_path / 'data/ota/Career.ipa').read_bytes() == b'PK synthetic export'
    assert len(calls) == 3
    token = (tmp_path / '.private/ota_token').read_text()
    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)
    with pytest.raises(subprocess.CalledProcessError):
        build(tmp_path, run=fail)
    assert (tmp_path / 'data/ota/build_number').read_text().strip() == '2'
    assert ota.info(tmp_path / 'data/ota')['build'] == '1'
    assert (tmp_path / '.private/ota_token').read_text() == token
