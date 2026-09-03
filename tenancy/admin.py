from django.contrib import admin

from tenancy.models import CompanyProfile, Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "is_design_partner", "onboarded_on")
    list_filter = ("is_active", "is_design_partner")
    search_fields = ("name", "slug")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active")
    list_filter = ("role", "is_active", "organization")


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "organization", "tin", "orc_registration_number",
                    "home_district")
    search_fields = ("legal_name", "tin", "orc_registration_number")
