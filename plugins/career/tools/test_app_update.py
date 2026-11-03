"""Published update metadata and the native banner's build-selection logic, without a device or signing."""
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

import ota
from runtime import CODE, write_json
from test_workflow import api, system


def test_stamp_offers_only_an_existing_published_install_with_a_scoped_token(system, api):
    root=system.root
    first=api('GET','/stamp')[1]
    assert 'build' not in first and 'install' not in first
    assert not (root/'.private/ota_token').exists()
    ipa=root/'synthetic.ipa';ipa.write_bytes(b'PK invented package bytes')
    ota.publish(ipa,'1.0','12','org.example.careerapp',root/'data/ota')
    assert 'install' not in api('GET','/stamp')[1]
    (root/'.private').mkdir(exist_ok=True)
    (root/'.private/ota_token').write_text('synthetic-install-capability')
    write_json(root/'settings.json',{'ota_base':'https://deploy.example.org'})
    view=api('GET','/stamp')[1]
    assert view['build']=='12' and view['stamp']!=first['stamp']
    assert 'synthetic-api-token' not in view['install']
    manifest=parse_qs(urlsplit(view['install']).query)['url'][0]
    assert manifest=='https://deploy.example.org/app/install/synthetic-install-capability/manifest.plist'
    ota.publish(ipa,'1.0','13','org.example.careerapp',root/'data/ota')
    assert api('GET','/stamp')[1]['build']=='13'
    assert api('GET','/stamp')[1]['stamp']!=view['stamp']
    write_json(root/'settings.json',{'ota_base':'http://deploy.example.org'})
    assert 'install' not in api('GET','/stamp')[1]
    (root/'data/ota/Career.ipa').unlink()
    assert 'build' not in api('GET','/stamp')[1]


@pytest.mark.skipif(sys.platform!='darwin',reason='Native Foundation build requires macOS')
def test_native_update_notice_tracks_the_running_and_dismissed_build(tmp_path):
    main=tmp_path/'main.swift'
    main.write_text('''import Foundation
let link = "itms-services://?action=download-manifest&url=https://deploy.example.org/app/install/example/manifest.plist"
func offer(_ build: String?, _ own: Int = 10, _ dismissed: Int = 0, _ install: String? = nil) -> AppUpdate? {
    AppUpdate.available(build:build,install:install ?? link,ownBuild:own,dismissedBuild:dismissed)
}
assert(offer("11")?.build == 11)
assert(offer("10") == nil && offer("9") == nil)
assert(offer("11", 10, 11) == nil)
assert(offer("12", 10, 11)?.build == 12)
assert(offer("11", 11) == nil)
assert(offer(nil) == nil && offer("unknown") == nil)
assert(offer("11", 10, 0, "https://deploy.example.org/") == nil)
assert(offer("11", 10, 0, link.replacingOccurrences(of:"https:",with:"http:")) == nil)
print("Update selection passed")
''')
    binary=tmp_path/'update-tests'
    result=subprocess.run(['xcrun','swiftc','-module-cache-path',str(tmp_path/'modules'),
                           str(CODE/'skills/jobs/examples/app/Sources/AppUpdate.swift'),str(main),'-o',str(binary)],
                          text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert subprocess.check_output([str(binary)],text=True).strip()=='Update selection passed'
