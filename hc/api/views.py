# -*- coding: utf-8 -*-
from datetime import timedelta as td

from django.db.models import F
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse

from hc.api import schemas
from hc.api.models import Check, Ping
from hc.api.decorators import check_api_key, uuid_or_400, validate_json
from hc.lib.badges import check_signature, get_badge_svg

# Create your views here.


@csrf_exempt
@uuid_or_400
@never_cache
def ping(request, code):
    try:
        check = Check.objects.get(code=code)
    except Check.DoesNotExist:
        return HttpResponseBadRequest()

    check.n_pings = F("n_pings") + 1
    check.last_ping = timezone.now()

    if check.status in ("new", "paused"):
        check.status = "up"

    check.save()
    check.refresh_from_db()

    _ping = Ping(owner=check)
    headers = request.META
    _ping.n = check.n_pings
    remote_addr = headers.get("HTTP_X_FORWARDED_FOR", headers["REMOTE_ADDR"])
    _ping.remote_addr = remote_addr.split(",")[0]
    _ping.scheme = headers.get("HTTP_X_FORWARDED_PROTO", "http")
    _ping.method = headers["REQUEST_METHOD"]
    # If User-Agent is longer than 200 characters, truncate it:
    _ping.ua = headers.get("HTTP_USER_AGENT", "")[:200]
    _ping.save()

    response = HttpResponse("OK")
    response["Access-Control-Allow-Origin"] = "*"
    return response


@csrf_exempt
@check_api_key
@validate_json(schemas.check)
def checks(request):
    if request.method == 'GET':
        q = Check.objects.filter(user=request.user)
        doc = {"checks": [check.to_dict() for check in q]}
        return JsonResponse(doc)
    elif request.method == 'POST':
        check = Check(user=request.user)
        check.name = str(request.json.get("name", ""))
        check.tags = str(request.json.get("tags", ""))
        if "timeout" in request.json:
            check.timeout = td(seconds=request.json["timeout"])
        if "grace" in request.json:
            check.grace = td(seconds=request.json["grace"])

        check.save()

        # This needs to be done after saving the check, because of
        # the M2M relation between checks and channels:
        if request.json.get("channels") == '*':
            check.assign_all_channels()

        return JsonResponse(check.to_dict(), status=201)

    # If request is neither GET nor POST, return "405 Method not allowed"
    return HttpResponse(status=405)


@csrf_exempt
@check_api_key
def pause(request, code):
    if request.method != "POST":
        # Method not allowed
        return HttpResponse(status=405)

    try:
        check = Check.objects.get(code=code, user=request.user)
    except Check.DoesNotExist:
        return HttpResponseBadRequest()

    check.status = "paused"
    check.save()

    return JsonResponse(check.to_dict())


@never_cache
def badge(request, username, signature, tag):
    if not check_signature(username, tag, signature):
        return HttpResponseBadRequest()

    status = "up"
    q = Check.objects.filter(user__username=username, tags__contains=tag)

    for check in q:
        if tag not in check.tags_list():
            continue

        if status == "up" and check.in_grace_period():
            status = "late"

        if check.get_status() == "down":
            status = "down"
            break

    svg = get_badge_svg(tag, status)
    return HttpResponse(svg, content_type="image/svg+xml")
