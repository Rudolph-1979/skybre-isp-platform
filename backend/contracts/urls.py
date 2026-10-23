from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ContractSettingsView, ContractTemplateViewSet, ContractViewSet, PublicContractDocumentView,
    PublicContractSignView, PublicContractView,
)

router = DefaultRouter()
router.register(r"contract-templates", ContractTemplateViewSet, basename="contract-template")
router.register(r"contracts", ContractViewSet, basename="contract")

urlpatterns = [
    path("contract-settings/", ContractSettingsView.as_view()),
    path("public/contract/<str:token>/", PublicContractView.as_view()),
    path("public/contract/<str:token>/document/", PublicContractDocumentView.as_view()),
    path("public/contract/<str:token>/sign/", PublicContractSignView.as_view()),
    path("", include(router.urls)),
]
