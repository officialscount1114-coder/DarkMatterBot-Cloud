"""Tor setup must not fight the Tor that is already running.

Reported from the field: pressing GET TOR while a campaign was running produced

    Downloaded tor-expert-bundle-windows-x86_64-...tar.gz ...
    Unpacking ...
    Could not unpack the Tor bundle: [Errno 13] Permission denied:
        'C:\\Users\\pc\\.mr_ads_tor\\tor-expert-bundle\\tor\\tor.exe'
    Could not fetch Tor automatically.

...even though Tor was up and sending traffic. The running tor.exe is locked by
Windows, so re-unpacking over it can only fail.
"""

import os

from core.tor_manager import download_tor_bundle, find_tor_in


def _install_fake_tor(dest):
    folder = dest / "tor-expert-bundle" / "tor"
    folder.mkdir(parents=True)
    binary = folder / ("tor.exe" if os.name == "nt" else "tor")
    binary.write_text("not a real binary")
    return binary


def test_existing_install_is_reused_without_downloading(tmp_path):
    binary = _install_fake_tor(tmp_path)
    messages: list = []

    found = download_tor_bundle(
        str(tmp_path), platform_name="windows-x86_64", log=messages.append
    )

    assert found == str(binary)
    assert any("already installed" in message for message in messages)
    # No archive was fetched, so nothing can collide with the running tor.exe.
    assert not any(path.suffix == ".gz" for path in tmp_path.iterdir())
    assert not any("Unpacking" in message for message in messages)


def test_find_tor_in_walks_into_the_bundle(tmp_path):
    binary = _install_fake_tor(tmp_path)
    assert find_tor_in(str(tmp_path)) == str(binary)


def test_find_tor_in_returns_empty_for_an_empty_folder(tmp_path):
    assert find_tor_in(str(tmp_path)) == ""
    assert find_tor_in(str(tmp_path / "does-not-exist")) == ""
