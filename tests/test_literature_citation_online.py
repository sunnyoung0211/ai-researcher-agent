"""Test optional citation HTTP checks without real network or model calls."""

import httpx
import pytest
import yaml

from airesearcher.services import literature
from airesearcher.stages.fakes.idea import fake_papers


@pytest.fixture
def citation(project, monkeypatch):
    paper = fake_papers('q')[0].model_copy(update={'url': 'https://arxiv.org/abs/test'})
    registry = project.root / 'idea/literature.jsonl'
    registry.write_text(paper.model_dump_json() + '\n', encoding='utf-8')
    requests = []
    client_type = httpx.Client

    def install(handler):
        def send(request):
            requests.append(request)
            return handler(request)
        monkeypatch.setattr(literature.httpx, 'Client', lambda **kw: client_type(
            transport=httpx.MockTransport(send), **kw))
    return project, paper, registry, requests, install


def check(citation):
    project, paper, _, _, _ = citation
    return literature.verify_citation(project.root, paper.paper_id, online=True)


def test_default_offline_and_registry_precedence_never_request(citation):
    project, paper, registry, requests, install = citation
    install(lambda r: pytest.fail('Unexpected HTTP request'))
    assert literature.verify_citation(project.root, paper.paper_id).status == 'verified'
    assert literature.verify_citation(project.root, 'unknown', online=True).status == 'not_in_archive'
    for update in ({'access_level': 'metadata_only'}, {'url': ''}):
        registry.write_text(paper.model_copy(update=update).model_dump_json() + '\n', encoding='utf-8')
        assert check(citation).status == 'metadata_only'
    assert not requests


def test_online_success_is_read_only(citation):
    project, _, _, requests, install = citation
    before = {str(p.relative_to(project.root)): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    install(lambda r: httpx.Response(200))
    result = check(citation)
    assert result.status == 'verified' and 'content claims are not verified' in result.note
    assert [r.method for r in requests] == ['HEAD']
    after = {str(p.relative_to(project.root)): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    assert after == before


@pytest.mark.parametrize('code', [403, 404, 429, 500])
def test_error_status_is_explicit(citation, code):
    *_, install = citation
    install(lambda r: httpx.Response(code))
    result = check(citation)
    assert result.status == 'source_unreachable' and str(code) in result.note


@pytest.mark.parametrize('code', [405, 501])
def test_head_fallback_streams_without_consuming_body(citation, code):
    class UnreadBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail('Response body must not be consumed')
            yield b''
    _, _, _, requests, install = citation
    install(lambda r: httpx.Response(code if r.method == 'HEAD' else 200, stream=UnreadBody()))
    assert check(citation).status == 'verified'
    assert [r.method for r in requests] == ['HEAD', 'GET']


@pytest.mark.parametrize('target', ['https://evil.test/paper', 'file:///etc/passwd',
                                  'https://user:secret@arxiv.org/paper'])
def test_redirect_targets_are_validated_before_contact(citation, target):
    _, _, _, requests, install = citation
    install(lambda r: httpx.Response(302, headers={'location': target}))
    assert check(citation).status == 'source_unreachable'
    assert len(requests) == 1


def test_relative_redirect_and_limit(citation):
    _, _, _, requests, install = citation
    install(lambda r: httpx.Response(302, headers={'location': '/next'}) if len(requests) == 1
            else httpx.Response(200))
    assert check(citation).status == 'verified'
    assert requests[-1].url.path == '/next'
    requests.clear()
    install(lambda r: httpx.Response(302, headers={'location': '/loop'}))
    assert check(citation).status == 'source_unreachable'
    assert len(requests) == 6


def test_timeout_and_permission_failure_do_not_leak_urls(citation):
    project, _, _, requests, install = citation
    def timeout(request):
        raise httpx.ReadTimeout('sensitive transport details', request=request)
    install(timeout)
    result = check(citation)
    assert result.status == 'source_unreachable' and 'sensitive' not in result.note
    cfg = project.root / 'project.yaml'
    data = yaml.safe_load(cfg.read_text(encoding='utf-8'))
    data['permissions']['network']['allow'] = []
    cfg.write_text(yaml.safe_dump(data), encoding='utf-8')
    requests.clear()
    assert check(citation).status == 'source_unreachable'
    assert not requests
    cfg.write_text('permissions: [invalid', encoding='utf-8')
    assert check(citation).status == 'source_unreachable'
    assert not requests
