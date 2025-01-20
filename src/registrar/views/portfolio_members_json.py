from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Value, F, CharField, TextField, Q, Case, When, OuterRef, Subquery
from django.db.models.functions import Cast, Coalesce, Concat
from django.contrib.postgres.aggregates import ArrayAgg
from django.urls import reverse
from django.views import View

from registrar.models.domain_invitation import DomainInvitation
from registrar.models.portfolio_invitation import PortfolioInvitation
from registrar.models.user_portfolio_permission import UserPortfolioPermission
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from registrar.views.utility.mixins import PortfolioMembersPermission
from registrar.models.utility.orm_helper import ArrayRemoveNull
from django.contrib.postgres.aggregates import StringAgg


class PortfolioMembersJson(PortfolioMembersPermission, View):

    def get(self, request):
        """Fetch members (permissions and invitations) for the given portfolio."""

        portfolio = request.GET.get("portfolio")

        # Two initial querysets which will be combined
        permissions = self.initial_permissions_search(portfolio)
        invitations = self.initial_invitations_search(portfolio)

        # Get total across both querysets before applying filters
        unfiltered_total = permissions.count() + invitations.count()

        permissions = self.apply_search_term(permissions, request)
        invitations = self.apply_search_term(invitations, request)

        # Union the two querysets
        objects = permissions.union(invitations)
        objects = self.apply_sorting(objects, request)

        paginator = Paginator(objects, 10)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        members = [self.serialize_members(request, portfolio, item, request.user) for item in page_obj.object_list]

        return JsonResponse(
            {
                "members": members,
                "UserPortfolioPermissionChoices": UserPortfolioPermissionChoices.to_dict(),
                "page": page_obj.number,
                "num_pages": paginator.num_pages,
                "has_previous": page_obj.has_previous(),
                "has_next": page_obj.has_next(),
                "total": paginator.count,
                "unfiltered_total": unfiltered_total,
            }
        )

    def initial_permissions_search(self, portfolio):
        """Perform initial search for permissions before applying any filters."""
        permissions = UserPortfolioPermission.objects.filter(portfolio=portfolio)
        permissions = (
            permissions.select_related("user")
            .annotate(
                first_name=F("user__first_name"),
                last_name=F("user__last_name"),
                email_display=F("user__email"),
                last_active=Coalesce(
                    Cast(F("user__last_login"), output_field=TextField()),  # Cast last_login to text
                    Value("Invalid date"),
                    output_field=TextField(),
                ),
                additional_permissions_display=F("additional_permissions"),
                member_display=Case(
                    # If email is present and not blank, use email
                    When(Q(user__email__isnull=False) & ~Q(user__email=""), then=F("user__email")),
                    # If first name or last name is present, use concatenation of first_name + " " + last_name
                    When(
                        Q(user__first_name__isnull=False) | Q(user__last_name__isnull=False),
                        then=Concat(
                            Coalesce(F("user__first_name"), Value("")),
                            Value(" "),
                            Coalesce(F("user__last_name"), Value("")),
                        ),
                    ),
                    # If neither, use an empty string
                    default=Value(""),
                    output_field=CharField(),
                ),
                domain_info=ArrayAgg(
                    # an array of domains, with id and name, colon separated
                    Concat(
                        F("user__permissions__domain_id"),
                        Value(":"),
                        F("user__permissions__domain__name"),
                        # specify the output_field to ensure union has same column types
                        output_field=CharField(),
                    ),
                    distinct=True,
                    filter=Q(user__permissions__domain__isnull=False)  # filter out null values
                    & Q(
                        user__permissions__domain__domain_info__portfolio=portfolio
                    ),  # only include domains in portfolio
                ),
                type=Value("member", output_field=CharField()),
            )
            .values(
                "id",
                "first_name",
                "last_name",
                "email_display",
                "last_active",
                "roles",
                "additional_permissions_display",
                "member_display",
                "domain_info",
                "type",
            )
        )
        return permissions

    def initial_invitations_search(self, portfolio):
        """Perform initial invitations search and get related DomainInvitation data based on the email."""

        # Subquery to get concatenated domain information for each email
        domain_invitations = (
            DomainInvitation.objects.filter(email=OuterRef("email"), domain__domain_info__portfolio=portfolio)
            .annotate(
                concatenated_info=Concat(F("domain__id"), Value(":"), F("domain__name"), output_field=CharField())
            )
            .values("concatenated_info")
        )

        concatenated_domain_info = (
            domain_invitations.values("email")
            .annotate(domain_info=StringAgg("concatenated_info", delimiter=", "))
            .values("domain_info")
        )

        # PortfolioInvitation query
        invitations = PortfolioInvitation.objects.filter(
            portfolio=portfolio, status=PortfolioInvitation.PortfolioInvitationStatus.INVITED
        )
        invitations = invitations.annotate(
            first_name=Value(None, output_field=CharField()),
            last_name=Value(None, output_field=CharField()),
            email_display=F("email"),
            last_active=Value("Invited", output_field=TextField()),
            additional_permissions_display=F("additional_permissions"),
            member_display=F("email"),
            # Use ArrayRemove to return an empty list when no domain invitations are found
            domain_info=ArrayRemoveNull(
                ArrayAgg(
                    # We've pre-concatenated the domain infos to limit the subquery to return a single virtual 'row',
                    # otherwise we'll trigger a "more than one row returned by a subquery used as an expression"
                    # when an email matches multiple domain invitations.
                    # We'll take care when processing the list of one single concatenated items item
                    # in serialize_members.
                    Subquery(concatenated_domain_info),
                    distinct=True,
                )
            ),
            type=Value("invitedmember", output_field=CharField()),
        ).values(
            "id",
            "first_name",
            "last_name",
            "email_display",
            "last_active",
            "roles",
            "additional_permissions_display",
            "member_display",
            "domain_info",
            "type",
        )

        return invitations

    def apply_search_term(self, queryset, request):
        """Apply search term to the queryset."""
        search_term = request.GET.get("search_term", "").lower()
        if search_term:
            queryset = queryset.filter(
                Q(first_name__icontains=search_term)
                | Q(last_name__icontains=search_term)
                | Q(email_display__icontains=search_term)
            )
        return queryset

    def apply_sorting(self, queryset, request):
        """Apply sorting to the queryset."""
        sort_by = request.GET.get("sort_by", "id")  # Default to 'id'
        order = request.GET.get("order", "asc")  # Default to 'asc'
        # Adjust sort_by to match the annotated fields in the unioned queryset
        if sort_by == "member":
            sort_by = "member_display"
        if order == "desc":
            queryset = queryset.order_by(F(sort_by).desc())
        else:
            queryset = queryset.order_by(sort_by)
        return queryset

    def serialize_members(self, request, portfolio, item, user):
        # Check if the user can edit other users
        user_can_edit_other_users = any(
            user.has_perm(perm) for perm in ["registrar.full_access_permission", "registrar.change_user"]
        )

        view_only = not user.has_edit_members_portfolio_permission(portfolio) or not user_can_edit_other_users

        is_admin = UserPortfolioRoleChoices.ORGANIZATION_ADMIN in (item.get("roles") or [])
        action_url = reverse(item["type"], kwargs={"pk": item["id"]})

        item_type = item.get("type", "")

        # Ensure domain_info is properly processed for invites -
        # we need to un-concatenate the subquery
        domain_info_list = item.get("domain_info", [])
        if item_type == "invitedmember" and isinstance(domain_info_list, list) and domain_info_list:
            # Split the first item in the list if it exists
            domain_info_list = domain_info_list[0].split(", ")

        # Serialize member data
        member_json = {
            "id": item.get("id", ""),  # id is id of UserPortfolioPermission or PortfolioInvitation
            "type": item_type,  # source is member or invitedmember
            "name": " ".join(filter(None, [item.get("first_name", ""), item.get("last_name", "")])),
            "email": item.get("email_display", ""),
            "member_display": item.get("member_display", ""),
            "roles": (item.get("roles") or []),
            "permissions": UserPortfolioPermission.get_portfolio_permissions(
                item.get("roles"), item.get("additional_permissions_display")
            ),
            # split domain_info array values into ids to form urls, and names
            "domain_urls": [
                reverse("domain", kwargs={"pk": domain_info.split(":")[0]}) for domain_info in domain_info_list
            ],
            "domain_names": [domain_info.split(":")[1] for domain_info in domain_info_list],
            "is_admin": is_admin,
            "last_active": item.get("last_active"),
            "action_url": action_url,
            "action_label": ("View" if view_only else "Manage"),
            "svg_icon": ("visibility" if view_only else "settings"),
        }
        return member_json
