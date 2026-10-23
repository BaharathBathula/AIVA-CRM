"""Publish to company social accounts through each network's official API.

Supported networks and what AIVA needs for each:

- LinkedIn: an OAuth access token with ``w_organization_social`` (company
  page) or ``w_member_social`` (personal profile), and the organization or
  person id. Posts via ``POST https://api.linkedin.com/rest/posts``.
- X (Twitter): an OAuth 2.0 user access token with ``tweet.write``.
  Posts via ``POST https://api.x.com/2/tweets``.
- Facebook Page: a Page access token with ``pages_manage_posts``.
  Posts via ``POST https://graph.facebook.com/v21.0/{page-id}/feed``.
- Instagram (business account): a token with ``instagram_content_publish``.
  Image posts via ``/{ig-user-id}/media`` then ``/media_publish``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
GRAPH = "https://graph.facebook.com/v21.0"
LINKEDIN_VERSION = "202409"

LIMITS = {"x": 280, "linkedin": 3000, "facebook": 63206, "instagram": 2200}


class SocialError(Exception):
    pass


@dataclass
class Published:
    external_id: str
    url: str | None


def _error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"{response.status_code} {response.text[:200]}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        for key in ("message", "detail", "title"):
            if body.get(key):
                return str(body[key])[:300]
        if body.get("errors"):
            return str(body["errors"])[:300]
    return f"{response.status_code} {str(body)[:200]}"


async def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise SocialError(f"Couldn't reach the network: {exc.__class__.__name__}.") from exc


def _little_text(text: str) -> str:
    """LinkedIn's "little text" format treats these characters as markup unless escaped."""

    out = text.replace("\\", "\\\\")
    for char in "|{}@[]()<>#*_~":
        out = out.replace(char, "\\" + char)
    return out


def _linkedin_author(external_id: str) -> str:
    if external_id.startswith("urn:li:"):
        return external_id
    return f"urn:li:organization:{external_id}" if external_id.isdigit() else f"urn:li:person:{external_id}"


async def verify(network: str, external_id: str, token: str) -> dict[str, str | None]:
    """Check the token works; returns {"name", "handle"} from the network."""

    if network == "facebook":
        response = await _request("GET", f"{GRAPH}/{external_id}", params={"fields": "name,username", "access_token": token})
        if response.status_code != 200:
            raise SocialError(_error(response))
        body = response.json()
        return {"name": body.get("name"), "handle": body.get("username")}
    if network == "instagram":
        response = await _request("GET", f"{GRAPH}/{external_id}", params={"fields": "username,name", "access_token": token})
        if response.status_code != 200:
            raise SocialError(_error(response))
        body = response.json()
        return {"name": body.get("name") or body.get("username"), "handle": body.get("username")}
    if network == "x":
        response = await _request("GET", "https://api.x.com/2/users/me", headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise SocialError(_error(response))
        data = response.json().get("data", {})
        return {"name": data.get("name"), "handle": data.get("username"), "id": data.get("id")}
    if network == "linkedin":
        author = _linkedin_author(external_id)
        headers = {"Authorization": f"Bearer {token}", "LinkedIn-Version": LINKEDIN_VERSION, "X-Restli-Protocol-Version": "2.0.0"}
        if ":organization:" in author:
            response = await _request("GET", f"https://api.linkedin.com/rest/organizations/{author.rsplit(':', 1)[1]}", headers=headers)
            if response.status_code != 200:
                raise SocialError(_error(response))
            body = response.json()
            return {"name": body.get("localizedName"), "handle": body.get("vanityName")}
        response = await _request("GET", "https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise SocialError(_error(response))
        body = response.json()
        return {"name": body.get("name"), "handle": body.get("email"), "id": body.get("sub")}
    raise SocialError("That network isn't supported.")


async def publish(network: str, external_id: str, token: str, text: str, link_url: str | None, image_url: str | None) -> Published:
    if network == "facebook":
        data = {"message": text, "access_token": token}
        if link_url:
            data["link"] = link_url
        response = await _request("POST", f"{GRAPH}/{external_id}/feed", data=data)
        if response.status_code != 200:
            raise SocialError(_error(response))
        post_id = response.json().get("id", "")
        return Published(post_id, f"https://www.facebook.com/{post_id}" if post_id else None)

    if network == "instagram":
        if not image_url:
            raise SocialError("Instagram posts need an image. Add an image link to the post.")
        caption = text + (f"\n\n{link_url}" if link_url else "")
        created = await _request("POST", f"{GRAPH}/{external_id}/media", data={"image_url": image_url, "caption": caption, "access_token": token})
        if created.status_code != 200:
            raise SocialError(_error(created))
        creation_id = created.json().get("id")
        published = await _request("POST", f"{GRAPH}/{external_id}/media_publish", data={"creation_id": creation_id, "access_token": token})
        if published.status_code != 200:
            raise SocialError(_error(published))
        media_id = published.json().get("id", "")
        link = await _request("GET", f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token})
        permalink = link.json().get("permalink") if link.status_code == 200 else None
        return Published(media_id, permalink)

    if network == "x":
        body_text = text if not link_url or link_url in text else f"{text} {link_url}"
        response = await _request("POST", "https://api.x.com/2/tweets", headers={"Authorization": f"Bearer {token}"}, json={"text": body_text})
        if response.status_code not in (200, 201):
            raise SocialError(_error(response))
        tweet_id = response.json().get("data", {}).get("id", "")
        return Published(tweet_id, f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None)

    if network == "linkedin":
        payload: dict[str, Any] = {
            "author": _linkedin_author(external_id),
            "commentary": _little_text(text),
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if link_url:
            payload["content"] = {"article": {"source": link_url, "title": text.split("\n", 1)[0][:200] or link_url}}
        response = await _request(
            "POST",
            "https://api.linkedin.com/rest/posts",
            headers={"Authorization": f"Bearer {token}", "LinkedIn-Version": LINKEDIN_VERSION, "X-Restli-Protocol-Version": "2.0.0"},
            json=payload,
        )
        if response.status_code not in (200, 201):
            raise SocialError(_error(response))
        post_urn = response.headers.get("x-restli-id") or response.headers.get("x-linkedin-id") or ""
        return Published(post_urn, f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else None)

    raise SocialError("That network isn't supported.")
