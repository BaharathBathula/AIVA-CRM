"""SCIM 2.0 protocol pieces shared by Microsoft Entra ID and Okta (RFC 7643 / 7644).

Pure functions only (no database): error and list responses, the discovery
documents (ServiceProviderConfig, ResourceTypes, Schemas), cleaning incoming
resources, ``eq`` filters, and PATCH operations in the shapes both identity
providers send:

* Entra: ``"op": "Replace"`` (capitalised), ``path`` with value filters such as
  ``emails[type eq "work"].value``, enterprise-extension paths, members removed
  with ``"path": "members", "value": [{"value": id}]``, and ``active`` sometimes
  sent as the string ``"False"``.
* Okta: ``"op": "replace"`` with value-only objects (``{"active": false}``,
  ``{"id": .., "displayName": ..}``) and ``members[value eq "id"]`` removals.

Attribute names and operators are matched case-insensitively, as the RFC requires.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any

from fastapi.responses import JSONResponse, Response

SCIM_MEDIA = "application/scim+json; charset=utf-8"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
SPC_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
RT_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"

MAX_RESULTS = 200  # page size cap (ServiceProviderConfig.filter.maxResults)
MAX_BODY_BYTES = 2_000_000
MAX_OPERATIONS = 500
MAX_RESOURCE_BYTES = 64_000  # stored attributes per user
MAX_STRING = 2_000
MAX_MULTI = 50  # values per multi-valued attribute (emails, phone numbers...)
MAX_MEMBERS = 20_000  # members per group


class ScimError(Exception):
    """An error answered in the SCIM error format."""

    def __init__(self, status: int, detail: str, scim_type: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        self.headers = headers or {}


def error_response(exc: ScimError) -> JSONResponse:
    body: dict[str, Any] = {"schemas": [ERROR_SCHEMA], "status": str(exc.status), "detail": exc.detail}
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return JSONResponse(body, status_code=exc.status, media_type=SCIM_MEDIA, headers=exc.headers)


def respond(body: dict[str, Any], status: int = 200, location: str | None = None) -> JSONResponse:
    headers = {"Location": location} if location else None
    return JSONResponse(body, status_code=status, media_type=SCIM_MEDIA, headers=headers)


def no_content() -> Response:
    return Response(status_code=204)


def list_response(resources: list[dict[str, Any]], total: int, start_index: int) -> dict[str, Any]:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": int(total),
        "startIndex": int(start_index),
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def parse_body(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BODY_BYTES:
        raise ScimError(413, "The request is too large.", "tooLarge")
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ScimError(400, "The request body isn't valid JSON.", "invalidSyntax") from exc
    if not isinstance(body, dict):
        raise ScimError(400, "The request body must be a JSON object.", "invalidSyntax")
    return body


def paging(start_index: str | None, count: str | None) -> tuple[int, int]:
    """1-based startIndex and a bounded count (RFC 7644 §3.4.2.4: bad values are clamped)."""

    try:
        start = max(1, int(start_index)) if start_index not in (None, "") else 1
    except ValueError:
        start = 1
    try:
        size = int(count) if count not in (None, "") else 100
    except ValueError:
        size = 100
    return start, max(0, min(MAX_RESULTS, size))


def project(resource: dict[str, Any], attributes: str | None, excluded: str | None) -> dict[str, Any]:
    """Honour ``attributes`` / ``excludedAttributes`` for top-level attributes (id and schemas always stay)."""

    if attributes:
        keep = {a.strip().split(".")[0].lower() for a in attributes.split(",") if a.strip()}
        keep |= {"id", "schemas", "meta"}
        return {k: v for k, v in resource.items() if k.lower() in keep}
    if excluded:
        drop = {a.strip().split(".")[0].lower() for a in excluded.split(",") if a.strip()} - {"id", "schemas"}
        return {k: v for k, v in resource.items() if k.lower() not in drop}
    return resource


# ---------------------------------------------------------------------------
# Attribute names
# ---------------------------------------------------------------------------

USER_ATTRS = (
    "userName", "name", "displayName", "nickName", "profileUrl", "title", "userType", "preferredLanguage", "locale",
    "timezone", "active", "emails", "phoneNumbers", "ims", "photos", "addresses", "entitlements", "roles",
    "x509Certificates", "externalId",
)
READ_ONLY = {"id", "meta", "schemas", "groups", "password"}
NAME_ATTRS = ("formatted", "familyName", "givenName", "middleName", "honorificPrefix", "honorificSuffix")
MULTI = ("emails", "phoneNumbers", "ims", "photos", "addresses", "entitlements", "roles", "x509Certificates")
MULTI_SUB = ("value", "display", "type", "primary", "$ref", "formatted", "streetAddress", "locality", "region", "postalCode", "country")
ENTERPRISE_ATTRS = ("employeeNumber", "costCenter", "organization", "division", "department", "manager")
GROUP_ATTRS = ("displayName", "externalId", "members")

_CANON = {a.lower(): a for a in (*USER_ATTRS, *READ_ONLY, *GROUP_ATTRS)}
_NAME = {a.lower(): a for a in NAME_ATTRS}
_SUB = {a.lower(): a for a in MULTI_SUB}
_ENT = {a.lower(): a for a in ENTERPRISE_ATTRS}
_MULTI_LC = {a.lower() for a in MULTI} | {"members"}

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def is_email(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 320 and bool(EMAIL_RE.match(value.strip()))


def canonical(name: str) -> str:
    return _CANON.get(name.lower(), name)


def truthy(value: Any, attribute: str = "active") -> bool:
    """SCIM booleans; Entra has sent ``"True"``/``"False"`` strings, so those are accepted too."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ScimError(400, f"{attribute} must be true or false.", "invalidValue")


def _string(value: Any, attribute: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ScimError(400, f"{attribute} must be a string.", "invalidValue")
    text = str(value)
    if len(text) > MAX_STRING:
        raise ScimError(400, f"{attribute} is too long.", "invalidValue")
    return text


def _clean_complex(value: Any, names: dict[str, str], attribute: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScimError(400, f"{attribute} must be an object.", "invalidValue")
    out: dict[str, Any] = {}
    for key, item in value.items():
        canon = names.get(str(key).lower())
        if canon is None or item is None:
            continue
        if canon == "primary":
            out[canon] = truthy(item, f"{attribute}.primary")
        else:
            cleaned = _string(item, f"{attribute}.{canon}")
            if cleaned is not None:
                out[canon] = cleaned
    return out


def _clean_multi(value: Any, attribute: str) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, Any]] = []
    for item in items:
        if item is None:
            continue
        if not isinstance(item, dict):
            item = {"value": item}
        cleaned = _clean_complex(item, _SUB, attribute)
        if cleaned:
            out.append(cleaned)
    if len(out) > MAX_MULTI:
        raise ScimError(400, f"Too many values for {attribute}.", "invalidValue")
    return out


def _clean_enterprise(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScimError(400, "The enterprise extension must be an object.", "invalidValue")
    out: dict[str, Any] = {}
    for key, item in value.items():
        canon = _ENT.get(str(key).lower())
        if canon is None or item is None:
            continue
        if canon == "manager":
            manager = _manager(item)
            if manager:
                out["manager"] = manager
        else:
            cleaned = _string(item, canon)
            if cleaned is not None:
                out[canon] = cleaned
    return out


def _manager(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        cleaned = _clean_complex(value, _SUB, "manager")
        return cleaned if cleaned.get("value") else None
    text = _string(value, "manager")
    return {"value": text} if text else None


def _clean_extension(value: Any, urn: str) -> dict[str, Any]:
    """Other schema extensions are kept as sent (JSON-safe, bounded) so they read back unchanged."""

    if not isinstance(value, dict):
        raise ScimError(400, f"{urn} must be an object.", "invalidValue")
    if len(json.dumps(value, default=str)) > 8_000:
        raise ScimError(400, f"{urn} is too large.", "invalidValue")
    return json.loads(json.dumps(value, default=str))


def set_attribute(resource: dict[str, Any], key: str, value: Any) -> None:
    """Set one top-level attribute (or extension block) from an incoming resource, cleaned."""

    lowered = key.lower()
    if lowered == ENTERPRISE.lower():
        cleaned = _clean_enterprise(value) if value is not None else {}
        if cleaned:
            resource[ENTERPRISE] = cleaned
        else:
            resource.pop(ENTERPRISE, None)
        return
    if lowered.startswith("urn:"):
        if lowered.startswith(USER_SCHEMA.lower() + ":"):
            set_attribute(resource, key[len(USER_SCHEMA) + 1:], value)
        elif value is None:
            resource.pop(key, None)
        else:
            resource[key] = _clean_extension(value, key)
        return
    canon = _CANON.get(lowered)
    if canon is None or canon in READ_ONLY:
        return  # unknown and read-only attributes are ignored
    if value is None:
        resource.pop(canon, None)
    elif canon == "active":
        resource["active"] = truthy(value)
    elif canon == "name":
        cleaned = _clean_complex(value, _NAME, "name")
        if cleaned:
            resource["name"] = cleaned
        else:
            resource.pop("name", None)
    elif canon in MULTI:
        cleaned_list = _clean_multi(value, canon)
        if cleaned_list:
            resource[canon] = cleaned_list
        else:
            resource.pop(canon, None)
    elif canon == "members":
        resource["members"] = clean_members(value)
    else:
        cleaned_text = _string(value, canon)
        if cleaned_text is None or cleaned_text == "":
            resource.pop(canon, None)
        else:
            resource[canon] = cleaned_text


def clean_user(body: dict[str, Any]) -> dict[str, Any]:
    """An incoming User, cleaned to the attributes AIVA stores (id, meta, password and groups dropped)."""

    resource: dict[str, Any] = {}
    for key, value in body.items():
        set_attribute(resource, str(key), value)
    check_size(resource)
    return resource


def clean_group(body: dict[str, Any]) -> dict[str, Any]:
    resource: dict[str, Any] = {}
    for key, value in body.items():
        canon = _CANON.get(str(key).lower())
        if canon in GROUP_ATTRS:
            set_attribute(resource, canon, value)
    return resource


def clean_members(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        member = item.get("value") if isinstance(item, dict) else item
        if member is None:
            continue
        text = str(member).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    if len(out) > MAX_MEMBERS:
        raise ScimError(400, f"A group can have at most {MAX_MEMBERS} members.", "invalidValue")
    return out


def check_size(resource: dict[str, Any]) -> None:
    if len(json.dumps(resource, default=str)) > MAX_RESOURCE_BYTES:
        raise ScimError(400, "This user has too much data to store.", "invalidValue")


# ---------------------------------------------------------------------------
# Filters (Entra only sends "eq" and "and"; Okta sends "eq")
# ---------------------------------------------------------------------------

_FILTER = re.compile(
    r"""^\s*(?P<attr>[A-Za-z][\w:.$-]*(?:\[[^\]]*\])?(?:\.[A-Za-z$][\w$]*)?)\s+(?P<op>eq)\s+"""
    r"""(?:"(?P<str>(?:[^"\\]|\\.)*)"|(?P<lit>true|false|null|-?\d+(?:\.\d+)?))\s*$""",
    re.IGNORECASE,
)


def _unescape(text: str) -> str:
    try:
        return json.loads(f'"{text}"')
    except ValueError:
        return text.replace('\\"', '"')


def parse_filter(text: str | None) -> list[tuple[str, Any]]:
    """``attr eq "value" [and attr eq "value"]`` → [(normalized attr, value)]. Anything else is invalidFilter."""

    if not text or not text.strip():
        return []
    if len(text) > 1000:
        raise ScimError(400, "The filter is too long.", "invalidFilter")
    parts = re.split(r"\s+and\s+(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", text.strip(), flags=re.IGNORECASE)
    out: list[tuple[str, Any]] = []
    for part in parts:
        match = _FILTER.match(part)
        if not match:
            raise ScimError(400, f"Unsupported filter: {part.strip()[:200]}. Use attribute eq \"value\".", "invalidFilter")
        attr = match.group("attr")
        value: Any
        if match.group("str") is not None:
            value = _unescape(match.group("str"))
        else:
            literal = match.group("lit").lower()
            value = {"true": True, "false": False, "null": None}.get(literal, literal)
        out.append((normalize_filter_attr(attr), value))
    return out


def normalize_filter_attr(attr: str) -> str:
    """``urn:...:User:userName`` → ``username``; ``emails[type eq "work"].value`` → ``emails.value``."""

    lowered = attr.lower()
    for prefix in (USER_SCHEMA.lower() + ":", GROUP_SCHEMA.lower() + ":"):
        lowered = lowered.removeprefix(prefix)
    lowered = re.sub(r"\[[^\]]*\]", "", lowered)
    if lowered == "emails":
        lowered = "emails.value"
    return lowered


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


class Path:
    """A parsed PATCH path: [urn:]attr[filter][.sub]."""

    def __init__(self, urn: str | None, attr: str, filters: list[tuple[str, Any]], sub: str | None):
        self.urn = urn
        self.attr = attr
        self.filters = filters
        self.sub = sub


_PATH = re.compile(r"^(?P<attr>[A-Za-z$][\w$-]*)(?:\[(?P<filter>[^\]]*)\])?(?:\.(?P<sub>[A-Za-z$][\w$]*))?$")


def parse_path(path: str) -> Path:
    raw = path.strip()
    urn: str | None = None
    lowered = raw.lower()
    if lowered.startswith("urn:"):
        core = next((p for p in (USER_SCHEMA, GROUP_SCHEMA) if lowered.startswith(p.lower() + ":")), None)
        if core is not None:
            raw = raw[len(core) + 1:]
        else:
            # urn:...:<Schema>:attr[.sub] — the attribute is after the last colon outside brackets.
            head = raw.split("[", 1)[0]
            cut = head.rfind(":")
            if cut <= 0:
                raise ScimError(400, f"Invalid path: {path[:200]}", "invalidPath")
            urn, raw = raw[:cut], raw[cut + 1:]
            if urn.lower() == ENTERPRISE.lower():
                urn = ENTERPRISE
    match = _PATH.match(raw)
    if not match:
        raise ScimError(400, f"Invalid path: {path[:200]}", "invalidPath")
    filters: list[tuple[str, Any]] = []
    if match.group("filter") is not None:
        for attr, value in parse_filter(match.group("filter")):
            filters.append((attr, value))
    return Path(urn, match.group("attr"), filters, match.group("sub"))


def _matches(item: dict[str, Any], filters: list[tuple[str, Any]]) -> bool:
    for attr, expected in filters:
        key = _SUB.get(attr.split(".")[-1], attr)
        actual = item.get(key)
        if isinstance(actual, str) and isinstance(expected, str):
            if actual.lower() != expected.lower():
                return False
        elif actual != expected:
            return False
    return True


def _filter_item(filters: list[tuple[str, Any]]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for attr, value in filters:
        key = _SUB.get(attr.split(".")[-1])
        if key:
            item[key] = value
    return item


def _value_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _apply_value_only(resource: dict[str, Any], op: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ScimError(400, f"A {op} without a path needs an object value.", "invalidValue")
    for key, item in value.items():
        key = str(key)
        lowered = key.lower()
        if lowered in {"id", "meta", "schemas"}:
            continue
        if lowered == ENTERPRISE.lower() and isinstance(item, dict):
            # Merge the extension's attributes one by one.
            for sub_key, sub_value in item.items():
                _apply_path(resource, op, Path(ENTERPRISE, str(sub_key), [], None), sub_value)
            continue
        _apply_path(resource, op, parse_path(key), item)


def apply_patch(resource: dict[str, Any], body: dict[str, Any], *, max_bytes: int | None = MAX_RESOURCE_BYTES) -> dict[str, Any]:
    """Apply a PatchOp request to a copy of ``resource`` (users; groups use the same engine) and return it."""

    schemas = [str(s).lower() for s in (body.get("schemas") or [])]
    if schemas and PATCH_SCHEMA.lower() not in schemas:
        raise ScimError(400, "Use the PatchOp schema for PATCH requests.", "invalidSyntax")
    operations = next((v for k, v in body.items() if str(k).lower() == "operations"), None)
    if not isinstance(operations, list) or not operations:
        raise ScimError(400, "Operations must be a non-empty list.", "invalidSyntax")
    if len(operations) > MAX_OPERATIONS:
        raise ScimError(400, f"Send at most {MAX_OPERATIONS} operations at a time.", "tooMany")
    result = copy.deepcopy(resource)
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimError(400, "Each operation must be an object.", "invalidSyntax")
        fields = {str(k).lower(): v for k, v in operation.items()}
        op = str(fields.get("op") or "").strip().lower()
        if op not in {"add", "replace", "remove"}:
            raise ScimError(400, f"Unsupported operation: {fields.get('op')!s:.40}.", "invalidSyntax")
        path = fields.get("path")
        value = fields.get("value")
        if path in (None, ""):
            if op == "remove":
                raise ScimError(400, "A remove operation needs a path.", "noTarget")
            _apply_value_only(result, op, value)
        else:
            _apply_path(result, op, parse_path(str(path)), value)
    if max_bytes is not None:
        check_size(result)
    return result


def _apply_path(resource: dict[str, Any], op: str, path: Path, value: Any) -> None:
    if path.urn == ENTERPRISE:
        _apply_enterprise(resource, op, path, value)
        return
    if path.urn:
        block = dict(resource.get(path.urn) or {})
        if op == "remove":
            block.pop(path.attr, None)
        else:
            block[path.attr] = json.loads(json.dumps(value, default=str))
        if block:
            resource[path.urn] = _clean_extension(block, path.urn)
        else:
            resource.pop(path.urn, None)
        return

    attr = canonical(path.attr)
    lowered = attr.lower()
    if attr in READ_ONLY and attr != "groups":
        if attr == "password":
            return  # never stored
        raise ScimError(400, f"{attr} can't be changed.", "mutability")
    if attr == "groups":
        return  # group membership is managed through /Groups
    if lowered in _MULTI_LC:
        _apply_multi(resource, op, attr, path, value)
        return
    if attr == "name":
        name = dict(resource.get("name") or {})
        if path.sub:
            sub = _NAME.get(path.sub.lower())
            if sub is None:
                raise ScimError(400, f"Unknown attribute name.{path.sub}.", "invalidPath")
            if op == "remove" or value in (None, ""):
                name.pop(sub, None)
            else:
                name[sub] = _string(value, f"name.{sub}")
        elif op == "remove":
            name = {}
        elif op == "add" and isinstance(value, dict):
            name.update(_clean_complex(value, _NAME, "name"))
        else:
            name = _clean_complex(value, _NAME, "name") if value is not None else {}
        if name:
            resource["name"] = name
        else:
            resource.pop("name", None)
        return
    if path.sub:
        raise ScimError(400, f"{attr} has no sub-attribute {path.sub}.", "invalidPath")
    if op == "remove":
        if attr == "userName":
            raise ScimError(400, "userName is required.", "mutability")
        resource.pop(attr, None)
        return
    set_attribute(resource, attr, value)


def _apply_enterprise(resource: dict[str, Any], op: str, path: Path, value: Any) -> None:
    block = dict(resource.get(ENTERPRISE) or {})
    attr = _ENT.get(path.attr.lower())
    if attr is None:
        raise ScimError(400, f"Unknown enterprise attribute {path.attr}.", "invalidPath")
    if op == "remove" or value in (None, ""):
        block.pop(attr, None)
    elif attr == "manager":
        if path.sub and path.sub.lower() == "value":
            value = {"value": value}
        manager = _manager(value)
        if manager:
            block["manager"] = manager
        else:
            block.pop("manager", None)
    else:
        block[attr] = _string(value, attr)
    if block:
        resource[ENTERPRISE] = block
    else:
        resource.pop(ENTERPRISE, None)


def _apply_multi(resource: dict[str, Any], op: str, attr: str, path: Path, value: Any) -> None:
    if attr == "members":
        _apply_members(resource, op, path, value)
        return
    items: list[dict[str, Any]] = [dict(i) for i in resource.get(attr) or []]
    sub = _SUB.get(path.sub.lower()) if path.sub else None
    if path.sub and sub is None:
        raise ScimError(400, f"Unknown attribute {attr}.{path.sub}.", "invalidPath")

    if path.filters:
        matched = [i for i in items if _matches(i, path.filters)]
        if op == "remove":
            if sub:
                for item in matched:
                    item.pop(sub, None)
                items = [i for i in items if i]
            else:
                items = [i for i in items if not _matches(i, path.filters)]
        elif not matched:
            # Entra adds a typed value with Add (or Replace) on emails[type eq "work"].value.
            new = _filter_item(path.filters)
            if sub:
                new[sub] = value if sub == "primary" else _string(value, f"{attr}.{sub}")
            elif isinstance(value, dict):
                new.update(_clean_complex(value, _SUB, attr))
            items.append(_clean_complex(new, _SUB, attr))
        else:
            for item in matched:
                if sub:
                    item[sub] = truthy(value, f"{attr}.primary") if sub == "primary" else _string(value, f"{attr}.{sub}")
                elif isinstance(value, dict):
                    if op == "replace":
                        item.clear()
                        item.update(_filter_item(path.filters))
                    item.update(_clean_complex(value, _SUB, attr))
    elif sub:
        if op == "remove":
            for item in items:
                item.pop(sub, None)
            items = [i for i in items if i]
        else:
            # emails.value without a filter: the primary value (or the only one).
            target = next((i for i in items if i.get("primary") is True), items[0] if items else None)
            if target is None:
                target = {}
                items.append(target)
            target[sub] = truthy(value, f"{attr}.primary") if sub == "primary" else _string(value, f"{attr}.{sub}")
    elif op == "remove":
        if value not in (None, [], ""):
            drop = {str(v.get("value") if isinstance(v, dict) else v).lower() for v in _value_list(value)}
            items = [i for i in items if str(i.get("value", "")).lower() not in drop]
        else:
            items = []
    elif op == "replace":
        items = _clean_multi(value, attr) if value is not None else []
    else:  # add: append, replacing values that are equal
        for new in _clean_multi(value, attr):
            items = [i for i in items if not (i.get("value") == new.get("value") and i.get("type") == new.get("type"))]
            items.append(new)

    if any(i.get("primary") is True for i in items):
        # At most one primary value; the most recent wins.
        last = max(index for index, i in enumerate(items) if i.get("primary") is True)
        for index, item in enumerate(items):
            if index != last and item.get("primary") is True:
                item["primary"] = False
    if len(items) > MAX_MULTI:
        raise ScimError(400, f"Too many values for {attr}.", "invalidValue")
    if items:
        resource[attr] = items
    else:
        resource.pop(attr, None)


def _apply_members(resource: dict[str, Any], op: str, path: Path, value: Any) -> None:
    current: list[str] = list(resource.get("members") or [])
    if op == "remove":
        if path.filters:
            drop = {str(v).lower() for a, v in path.filters if a.split(".")[-1] == "value"}
        elif value not in (None, [], ""):
            drop = {m.lower() for m in clean_members(value)}
        else:
            drop = None  # remove every member
        current = [] if drop is None else [m for m in current if m.lower() not in drop]
    elif op == "replace":
        current = clean_members(value) if value is not None else []
    else:
        seen = {m.lower() for m in current}
        for member in clean_members(value):
            if member.lower() not in seen:
                seen.add(member.lower())
                current.append(member)
    if len(current) > MAX_MEMBERS:
        raise ScimError(400, f"A group can have at most {MAX_MEMBERS} members.", "invalidValue")
    resource["members"] = current


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------


def service_provider_config(base_url: str, docs_url: str) -> dict[str, Any]:
    return {
        "schemas": [SPC_SCHEMA],
        "documentationUri": docs_url,
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_RESULTS},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "A SCIM token created on the integration's page in AIVA (aiva_scim_...).",
                "primary": True,
            }
        ],
        "meta": {"resourceType": "ServiceProviderConfig", "location": f"{base_url}/ServiceProviderConfig"},
    }


def resource_types(base_url: str) -> list[dict[str, Any]]:
    return [
        {
            "schemas": [RT_SCHEMA],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "description": "AIVA workspace members",
            "schema": USER_SCHEMA,
            "schemaExtensions": [{"schema": ENTERPRISE, "required": False}],
            "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/User"},
        },
        {
            "schemas": [RT_SCHEMA],
            "id": "Group",
            "name": "Group",
            "endpoint": "/Groups",
            "description": "Identity provider groups, mapped to AIVA roles and teams",
            "schema": GROUP_SCHEMA,
            "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/Group"},
        },
    ]


def _attr(name: str, kind: str = "string", *, multi: bool = False, required: bool = False, unique: str = "none",
          mutability: str = "readWrite", sub: list[dict[str, Any]] | None = None, case_exact: bool = False, returned: str = "default") -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name, "type": kind, "multiValued": multi, "description": name, "required": required,
        "caseExact": case_exact, "mutability": mutability, "returned": returned, "uniqueness": unique,
    }
    if sub:
        out["subAttributes"] = sub
    return out


def _multi_sub(types: list[str] | None = None) -> list[dict[str, Any]]:
    subs = [_attr("value"), _attr("display"), _attr("type"), _attr("primary", "boolean")]
    if types:
        subs[2]["canonicalValues"] = types
    return subs


def schemas(base_url: str) -> list[dict[str, Any]]:
    user = {
        "schemas": [SCHEMA_SCHEMA],
        "id": USER_SCHEMA,
        "name": "User",
        "description": "User account",
        "attributes": [
            _attr("userName", required=True, unique="server"),
            _attr("name", "complex", sub=[_attr(n) for n in NAME_ATTRS]),
            _attr("displayName"),
            _attr("nickName"),
            _attr("title"),
            _attr("userType"),
            _attr("preferredLanguage"),
            _attr("locale"),
            _attr("timezone"),
            _attr("active", "boolean"),
            _attr("emails", "complex", multi=True, sub=_multi_sub(["work", "home", "other"])),
            _attr("phoneNumbers", "complex", multi=True, sub=_multi_sub(["work", "home", "mobile", "fax", "pager", "other"])),
            _attr("addresses", "complex", multi=True, sub=[_attr(n) for n in ("formatted", "streetAddress", "locality", "region", "postalCode", "country", "type")] + [_attr("primary", "boolean")]),
            _attr("roles", "complex", multi=True, sub=_multi_sub()),
            _attr("groups", "complex", multi=True, mutability="readOnly", sub=[_attr("value", mutability="readOnly"), _attr("display", mutability="readOnly")]),
            _attr("externalId", case_exact=True),
        ],
        "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{USER_SCHEMA}"},
    }
    enterprise = {
        "schemas": [SCHEMA_SCHEMA],
        "id": ENTERPRISE,
        "name": "EnterpriseUser",
        "description": "Enterprise user",
        "attributes": [_attr(n) for n in ("employeeNumber", "costCenter", "organization", "division", "department")]
        + [_attr("manager", "complex", sub=[_attr("value"), _attr("$ref", "reference"), _attr("displayName", mutability="readOnly")])],
        "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{ENTERPRISE}"},
    }
    group = {
        "schemas": [SCHEMA_SCHEMA],
        "id": GROUP_SCHEMA,
        "name": "Group",
        "description": "Group",
        "attributes": [
            _attr("displayName", required=True, unique="server"),
            _attr("members", "complex", multi=True, sub=[_attr("value", mutability="immutable"), _attr("display", mutability="readOnly"), _attr("$ref", "reference", mutability="immutable")]),
            _attr("externalId", case_exact=True),
        ],
        "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{GROUP_SCHEMA}"},
    }
    return [user, enterprise, group]
