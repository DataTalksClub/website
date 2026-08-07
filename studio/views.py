from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .auth import staff_required


@staff_required
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "studio/home.html")
