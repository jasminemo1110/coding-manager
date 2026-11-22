from collections import OrderedDict
from unittest.mock import Mock

import pytest
import requests

import github_releases
from app import app, scanner


@pytest.fixture(autouse=True)
def isolated_release_cache(monkeypatch):
    monkeypatch.setattr(github_releases, '_cache', OrderedDict())


def release(tag, published_at, *, body=None, draft=False, prerelease=False, name=None):
    return dict(
        tag_name=tag, published_at=published_at, draft=draft,
        prerelease=prerelease, body=body, name=name,
    )


def add_project(db, repo, visibility, public_repo=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, github_repo, github_visibility, github_repo_public) "
            "VALUES ('Project', ?, ?, ?)", (repo, visibility, public_repo),
        )
        return f"/project/{cur.lastrowid}"


def page(items, *, has_next=False):
    response = Mock()
    response.json.return_value = items
    response.links = {"next": {}} if has_next else {}
    return response


def test_release_section_counts_all_pages_and_refreshes_public_mirror(test_db, monkeypatch):
    path = add_project(test_db, 'example/private', 'private', 'example/mirror')
    calls = []
    older = release('v1', '2026-10-15T00:00:00Z')
    beta = release(
        'v2-beta', '2026-11-20T00:00:00Z', prerelease=True, name='Beta',
        body='- **更新说明** [链接](/example/mirror/issues/1)\n\n<iframe src="/settings"></iframe>',
    )
    responses = iter([
        page([older, release('draft', None, draft=True)], has_next=True),
        page([beta]),
        page([release('v3', '2026-11-22T00:00:00Z', body='New since last refresh'), beta, older]),
    ])

    def get(session, url, *, params, headers, timeout):
        assert session.trust_env is False
        assert 'Authorization' not in headers
        calls.append((url, params))
        return next(responses)

    monkeypatch.setattr(requests.Session, 'get', get)
    monkeypatch.setattr(scanner, 'github_repo_metadata', lambda *_: {'visibility': 'private'})
    client = app.test_client()
    assert f'data-url="{path}/releases"' in client.get(path).text
    assert calls == []

    html = client.get(f'{path}/releases').text
    assert '共 2 个版本' in html and 'draft' not in html
    assert '<details class="release-row" open>' in html
    assert '<details class="release-row">' in html
    assert html.index('v2-beta') < html.index('v1')
    assert '预发布' in html and '2026-11-20' in html
    assert '<strong>更新说明</strong>' in html
    assert 'https://github.com/example/mirror/issues/1' in html
    assert 'https://github.com/example/mirror/releases/tag/v2-beta' in html
    assert '<iframe' not in html and '&lt;iframe' in html
    assert '没有填写更新说明' in html
    assert client.get(f'{path}/releases').text == html
    url = 'https://api.github.com/repos/example/mirror/releases'
    assert calls == [(url, {'per_page': 100, 'page': 1}), (url, {'per_page': 100, 'page': 2})]

    assert client.post(f'{path}/refresh-github').json['ok']
    assert calls[-1] == (url, {'per_page': 100, 'page': 1})
    refreshed = client.get(f'{path}/releases').text
    assert '共 3 个版本' in refreshed and 'New since last refresh' in refreshed
    assert len(calls) == 3


def test_release_section_distinguishes_private_empty_and_failed_repositories(test_db, monkeypatch):
    private = add_project(test_db, 'example/other-private', 'private')
    public = add_project(test_db, 'example/single-public', 'public')
    get = Mock(side_effect=[page([]), requests.Timeout()])
    monkeypatch.setattr(requests.Session, 'get', get)
    monkeypatch.setattr(scanner, 'github_repo_metadata', lambda *_: {'visibility': 'public'})
    client = app.test_client()
    assert 'id="project-releases"' not in client.get(private).text
    assert client.get(f'{private}/releases').status_code == 404
    get.assert_not_called()

    assert 'id="project-releases"' in client.get(public).text
    assert '共 0 个版本' in client.get(f'{public}/releases').text
    assert get.call_args.args == ('https://api.github.com/repos/example/single-public/releases',)
    assert client.post(f'{public}/refresh-github').json['ok']
    html = client.get(f'{public}/releases').text
    assert '暂时无法读取发布记录' in html
    assert '共 0 个版本' not in html
