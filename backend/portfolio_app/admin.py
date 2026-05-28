"""Django admin registrations -- handy during Phase 1 for eyeballing the DB."""

from django.contrib import admin

from .models import AuditEntry, GroupConfig, Holding, Setting, Transaction


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ("kind", "asset_class", "group", "ticker", "name", "currency")
    list_filter = ("kind", "asset_class", "group")
    search_fields = ("ticker", "name")


@admin.register(GroupConfig)
class GroupConfigAdmin(admin.ModelAdmin):
    list_display = ("asset_class", "name", "target_weight", "description")
    list_filter = ("asset_class",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "ticker",
        "group",
        "action",
        "amount_eur",
        "shares",
        "listing_currency",
    )
    list_filter = ("action", "listing_currency", "group")
    search_fields = ("ticker", "note")
    date_hierarchy = "date"


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "method", "endpoint")
    list_filter = ("method",)
    readonly_fields = ("timestamp", "user", "method", "endpoint", "payload_diff")


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_at")
