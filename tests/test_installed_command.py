
from core.application import CoreApplication
from core.entrypoint import default_data, main


def test_default_data_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv('CORE_WEB_DATA', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    first = default_data()
    monkeypatch.chdir(tmp_path)
    assert default_data() == first == tmp_path/'local/RISARMS/CORE/web'


def test_host_start_uses_stable_data_and_explicit_config(monkeypatch, tmp_path):
    captured = {}
    def serve(app, **kwargs):
        captured.update(kwargs)
        assert app._resolve_config_path() is None
    monkeypatch.setattr('core.web.gateway.serve', serve)
    monkeypatch.setenv('CORE_WEB_DATA', str(tmp_path/'data'))
    assert main(['--no-browser']) == 0
    assert captured == {'directory': tmp_path/'data', 'port':8780, 'open_browser':False}


def test_explicit_configuration_still_resolves(tmp_path):
    config=tmp_path/'core.yaml'
    assert CoreApplication(config_path=config, discover_config=False)._resolve_config_path() == config


def test_doctor_does_not_start_network_or_create_data(tmp_path, capsys):
    target=tmp_path/'missing'
    assert main(['doctor','--data',str(target)]) == 0
    assert not target.exists()
    assert str(target) in capsys.readouterr().out
