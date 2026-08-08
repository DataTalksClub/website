from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST


@require_GET
def csrf_seed(request: HttpRequest) -> HttpResponse:
    return HttpResponse(get_token(request), content_type="text/plain")


@csrf_protect
@require_POST
def high_risk_post(request: HttpRequest) -> HttpResponse:
    del request
    return HttpResponse("fixture reached", content_type="text/plain")
