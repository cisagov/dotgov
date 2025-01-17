import json
import logging

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.contrib import messages
from registrar.forms import portfolio as portfolioForms
from registrar.models import Portfolio, User
from registrar.models.domain import Domain
from registrar.models.domain_invitation import DomainInvitation
from registrar.models.portfolio_invitation import PortfolioInvitation
from registrar.models.user_domain_role import UserDomainRole
from registrar.models.user_portfolio_permission import UserPortfolioPermission
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from registrar.utility.email import EmailSendingError
from registrar.utility.email_invitations import send_domain_invitation_email, send_portfolio_invitation_email
from registrar.utility.errors import MissingEmailError
from registrar.utility.enums import DefaultUserValues
from registrar.views.utility.mixins import PortfolioMemberPermission
from registrar.views.utility.permission_views import (
    PortfolioDomainRequestsPermissionView,
    PortfolioDomainsPermissionView,
    PortfolioBasePermissionView,
    NoPortfolioDomainsPermissionView,
    PortfolioMemberDomainsPermissionView,
    PortfolioMemberDomainsEditPermissionView,
    PortfolioMemberEditPermissionView,
    PortfolioMemberPermissionView,
    PortfolioMembersPermissionView,
)
from django.views.generic import View
from django.views.generic.edit import FormMixin
from django.db import IntegrityError

from registrar.views.utility.invitation_helper import get_org_membership


logger = logging.getLogger(__name__)


class PortfolioDomainsView(PortfolioDomainsPermissionView, View):

    template_name = "portfolio_domains.html"

    def get(self, request):
        context = {}
        if self.request and self.request.user and self.request.user.is_authenticated:
            context["user_domain_count"] = self.request.user.get_user_domain_ids(request).count()
            context["num_expiring_domains"] = request.user.get_num_expiring_domains(request)

        return render(request, "portfolio_domains.html", context)


class PortfolioDomainRequestsView(PortfolioDomainRequestsPermissionView, View):

    template_name = "portfolio_requests.html"

    def get(self, request):
        return render(request, "portfolio_requests.html")


class PortfolioMemberView(PortfolioMemberPermissionView, View):

    template_name = "portfolio_member.html"

    def get(self, request, pk):
        portfolio_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        member = portfolio_permission.user

        # We have to explicitely name these with member_ otherwise we'll have conflicts with context preprocessors
        member_has_view_all_requests_portfolio_permission = member.has_view_all_requests_portfolio_permission(
            portfolio_permission.portfolio
        )
        member_has_edit_request_portfolio_permission = member.has_edit_request_portfolio_permission(
            portfolio_permission.portfolio
        )
        member_has_view_members_portfolio_permission = member.has_view_members_portfolio_permission(
            portfolio_permission.portfolio
        )
        member_has_edit_members_portfolio_permission = member.has_edit_members_portfolio_permission(
            portfolio_permission.portfolio
        )

        return render(
            request,
            self.template_name,
            {
                "edit_url": reverse("member-permissions", args=[pk]),
                "domains_url": reverse("member-domains", args=[pk]),
                "portfolio_permission": portfolio_permission,
                "member": member,
                "member_has_view_all_requests_portfolio_permission": member_has_view_all_requests_portfolio_permission,
                "member_has_edit_request_portfolio_permission": member_has_edit_request_portfolio_permission,
                "member_has_view_members_portfolio_permission": member_has_view_members_portfolio_permission,
                "member_has_edit_members_portfolio_permission": member_has_edit_members_portfolio_permission,
            },
        )


class PortfolioMemberDeleteView(PortfolioMemberPermission, View):

    def post(self, request, pk):
        """
        Find and delete the portfolio member using the provided primary key (pk).
        Redirect to a success page after deletion (or any other appropriate page).
        """
        portfolio_member_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        member = portfolio_member_permission.user

        active_requests_count = member.get_active_requests_count_in_portfolio(request)

        support_url = "https://get.gov/contact/"

        error_message = ""

        if active_requests_count > 0:
            # If they have any in progress requests
            error_message = mark_safe(  # nosec
                f"This member has an active domain request and can't be removed from the organization. "
                f"<a class='usa-link' href='{support_url}' target='_blank'>"
                "Contact the .gov team</a> to remove them."
            )
        elif member.is_only_admin_of_portfolio(portfolio_member_permission.portfolio):
            # If they are the last manager of a domain
            error_message = (
                "There must be at least one admin in your organization. Give another member admin "
                "permissions, make sure they log into the registrar, and then remove this member."
            )

        # From the Members Table page Else the Member Page
        if error_message:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse(
                    {"error": error_message},
                    status=400,
                )
            else:
                messages.error(request, error_message)
                return redirect(reverse("member", kwargs={"pk": pk}))

        # passed all error conditions
        portfolio_member_permission.delete()

        # From the Members Table page Else the Member Page
        success_message = f"You've removed {member.email} from the organization."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": success_message}, status=200)
        else:
            messages.success(request, success_message)
            return redirect(reverse("members"))


class PortfolioMemberEditView(PortfolioMemberEditPermissionView, View):

    template_name = "portfolio_member_permissions.html"
    form_class = portfolioForms.PortfolioMemberForm

    def get(self, request, pk):
        portfolio_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        user = portfolio_permission.user

        form = self.form_class(instance=portfolio_permission)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "member": user,
            },
        )

    def post(self, request, pk):
        portfolio_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        user_initially_is_admin = UserPortfolioRoleChoices.ORGANIZATION_ADMIN in portfolio_permission.roles
        user = portfolio_permission.user
        form = self.form_class(request.POST, instance=portfolio_permission)
        if form.is_valid():
            # Check if user is removing their own admin or edit role
            removing_admin_role_on_self = (
                request.user == user
                and user_initially_is_admin
                and UserPortfolioRoleChoices.ORGANIZATION_ADMIN not in form.cleaned_data.get("role", [])
            )
            form.save()
            messages.success(self.request, "The member access and permission changes have been saved.")
            return redirect("member", pk=pk) if not removing_admin_role_on_self else redirect("home")

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "member": user,  # Pass the user object again to the template
            },
        )


class PortfolioMemberDomainsView(PortfolioMemberDomainsPermissionView, View):

    template_name = "portfolio_member_domains.html"

    def get(self, request, pk):
        portfolio_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        member = portfolio_permission.user

        return render(
            request,
            self.template_name,
            {
                "portfolio_permission": portfolio_permission,
                "member": member,
            },
        )


class PortfolioMemberDomainsEditView(PortfolioMemberDomainsEditPermissionView, View):

    template_name = "portfolio_member_domains_edit.html"

    def get(self, request, pk):
        portfolio_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        member = portfolio_permission.user

        return render(
            request,
            self.template_name,
            {
                "portfolio_permission": portfolio_permission,
                "member": member,
            },
        )

    def post(self, request, pk):
        """
        Handles adding and removing domains for a portfolio member.
        """
        added_domains = request.POST.get("added_domains")
        removed_domains = request.POST.get("removed_domains")
        portfolio_permission = get_object_or_404(UserPortfolioPermission, pk=pk)
        member = portfolio_permission.user
        portfolio = portfolio_permission.portfolio

        added_domain_ids = self._parse_domain_ids(added_domains, "added domains")
        if added_domain_ids is None:
            return redirect(reverse("member-domains", kwargs={"pk": pk}))

        removed_domain_ids = self._parse_domain_ids(removed_domains, "removed domains")
        if removed_domain_ids is None:
            return redirect(reverse("member-domains", kwargs={"pk": pk}))

        if added_domain_ids or removed_domain_ids:
            try:
                self._process_added_domains(added_domain_ids, member, request.user, portfolio)
                self._process_removed_domains(removed_domain_ids, member)
                messages.success(request, "The domain assignment changes have been saved.")
                return redirect(reverse("member-domains", kwargs={"pk": pk}))
            except IntegrityError:
                messages.error(
                    request,
                    "A database error occurred while saving changes. If the issue persists, "
                    f"please contact {DefaultUserValues.HELP_EMAIL}.",
                )
                logger.error("A database error occurred while saving changes.", exc_info=True)
                return redirect(reverse("member-domains-edit", kwargs={"pk": pk}))
            except Exception as e:
                messages.error(
                    request,
                    f"An unexpected error occurred: {str(e)}. If the issue persists, "
                    f"please contact {DefaultUserValues.HELP_EMAIL}.",
                )
                logger.error(f"An unexpected error occurred: {str(e)}", exc_info=True)
                return redirect(reverse("member-domains-edit", kwargs={"pk": pk}))
        else:
            messages.info(request, "No changes detected.")
            return redirect(reverse("member-domains", kwargs={"pk": pk}))

    def _parse_domain_ids(self, domain_data, domain_type):
        """
        Parses the domain IDs from the request and handles JSON errors.
        """
        try:
            return json.loads(domain_data) if domain_data else []
        except json.JSONDecodeError:
            messages.error(
                self.request,
                f"Invalid data for {domain_type}. If the issue persists, "
                f"please contact {DefaultUserValues.HELP_EMAIL}.",
            )
            logger.error(f"Invalid data for {domain_type}")
            return None

    def _process_added_domains(self, added_domain_ids, member, requestor, portfolio):
        """
        Processes added domains by bulk creating UserDomainRole instances.
        """
        if added_domain_ids:
            # get added_domains from ids to pass to send email method and bulk create
            added_domains = Domain.objects.filter(id__in=added_domain_ids)
            member_of_a_different_org, _ = get_org_membership(portfolio, member.email, member)
            send_domain_invitation_email(
                email=member.email,
                requestor=requestor,
                domains=added_domains,
                is_member_of_different_org=member_of_a_different_org,
                requested_user=member,
            )
            # Bulk create UserDomainRole instances for added domains
            UserDomainRole.objects.bulk_create(
                [
                    UserDomainRole(domain=domain, user=member, role=UserDomainRole.Roles.MANAGER)
                    for domain in added_domains
                ],
                ignore_conflicts=True,  # Avoid duplicate entries
            )

    def _process_removed_domains(self, removed_domain_ids, member):
        """
        Processes removed domains by deleting corresponding UserDomainRole instances.
        """
        if removed_domain_ids:
            # Delete UserDomainRole instances for removed domains
            UserDomainRole.objects.filter(domain_id__in=removed_domain_ids, user=member).delete()


class PortfolioInvitedMemberView(PortfolioMemberPermissionView, View):

    template_name = "portfolio_member.html"
    # form_class = PortfolioInvitedMemberForm

    def get(self, request, pk):
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)
        # form = self.form_class(instance=portfolio_invitation)

        # We have to explicitely name these with member_ otherwise we'll have conflicts with context preprocessors
        member_has_view_all_requests_portfolio_permission = (
            UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS in portfolio_invitation.get_portfolio_permissions()
        )
        member_has_edit_request_portfolio_permission = (
            UserPortfolioPermissionChoices.EDIT_REQUESTS in portfolio_invitation.get_portfolio_permissions()
        )
        member_has_view_members_portfolio_permission = (
            UserPortfolioPermissionChoices.VIEW_MEMBERS in portfolio_invitation.get_portfolio_permissions()
        )
        member_has_edit_members_portfolio_permission = (
            UserPortfolioPermissionChoices.EDIT_MEMBERS in portfolio_invitation.get_portfolio_permissions()
        )

        return render(
            request,
            self.template_name,
            {
                "edit_url": reverse("invitedmember-permissions", args=[pk]),
                "domains_url": reverse("invitedmember-domains", args=[pk]),
                "portfolio_invitation": portfolio_invitation,
                "member_has_view_all_requests_portfolio_permission": member_has_view_all_requests_portfolio_permission,
                "member_has_edit_request_portfolio_permission": member_has_edit_request_portfolio_permission,
                "member_has_view_members_portfolio_permission": member_has_view_members_portfolio_permission,
                "member_has_edit_members_portfolio_permission": member_has_edit_members_portfolio_permission,
            },
        )


class PortfolioInvitedMemberDeleteView(PortfolioMemberPermission, View):

    def post(self, request, pk):
        """
        Find and delete the portfolio invited member using the provided primary key (pk).
        Redirect to a success page after deletion (or any other appropriate page).
        """
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)

        portfolio_invitation.delete()

        success_message = f"You've removed {portfolio_invitation.email} from the organization."
        # From the Members Table page Else the Member Page
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": success_message}, status=200)
        else:
            messages.success(request, success_message)
            return redirect(reverse("members"))


class PortfolioInvitedMemberEditView(PortfolioMemberEditPermissionView, View):

    template_name = "portfolio_member_permissions.html"
    form_class = portfolioForms.PortfolioInvitedMemberForm

    def get(self, request, pk):
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)
        form = self.form_class(instance=portfolio_invitation)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "invitation": portfolio_invitation,
            },
        )

    def post(self, request, pk):
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)
        form = self.form_class(request.POST, instance=portfolio_invitation)
        if form.is_valid():
            form.save()
            messages.success(self.request, "The member access and permission changes have been saved.")
            return redirect("invitedmember", pk=pk)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "invitation": portfolio_invitation,  # Pass the user object again to the template
            },
        )


class PortfolioInvitedMemberDomainsView(PortfolioMemberDomainsPermissionView, View):

    template_name = "portfolio_member_domains.html"

    def get(self, request, pk):
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)

        return render(
            request,
            self.template_name,
            {
                "portfolio_invitation": portfolio_invitation,
            },
        )


class PortfolioInvitedMemberDomainsEditView(PortfolioMemberDomainsEditPermissionView, View):

    template_name = "portfolio_member_domains_edit.html"

    def get(self, request, pk):
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)

        return render(
            request,
            self.template_name,
            {
                "portfolio_invitation": portfolio_invitation,
            },
        )

    def post(self, request, pk):
        """
        Handles adding and removing domains for a portfolio invitee.
        """
        added_domains = request.POST.get("added_domains")
        removed_domains = request.POST.get("removed_domains")
        portfolio_invitation = get_object_or_404(PortfolioInvitation, pk=pk)
        email = portfolio_invitation.email
        portfolio = portfolio_invitation.portfolio

        added_domain_ids = self._parse_domain_ids(added_domains, "added domains")
        if added_domain_ids is None:
            return redirect(reverse("invitedmember-domains", kwargs={"pk": pk}))

        removed_domain_ids = self._parse_domain_ids(removed_domains, "removed domains")
        if removed_domain_ids is None:
            return redirect(reverse("invitedmember-domains", kwargs={"pk": pk}))

        if added_domain_ids or removed_domain_ids:
            try:
                self._process_added_domains(added_domain_ids, email, request.user, portfolio)
                self._process_removed_domains(removed_domain_ids, email)
                messages.success(request, "The domain assignment changes have been saved.")
                return redirect(reverse("invitedmember-domains", kwargs={"pk": pk}))
            except IntegrityError:
                messages.error(
                    request,
                    "A database error occurred while saving changes. If the issue persists, "
                    f"please contact {DefaultUserValues.HELP_EMAIL}.",
                )
                logger.error("A database error occurred while saving changes.", exc_info=True)
                return redirect(reverse("invitedmember-domains-edit", kwargs={"pk": pk}))
            except Exception as e:
                messages.error(
                    request,
                    f"An unexpected error occurred: {str(e)}. If the issue persists, "
                    f"please contact {DefaultUserValues.HELP_EMAIL}.",
                )
                logger.error(f"An unexpected error occurred: {str(e)}.", exc_info=True)
                return redirect(reverse("invitedmember-domains-edit", kwargs={"pk": pk}))
        else:
            messages.info(request, "No changes detected.")
            return redirect(reverse("invitedmember-domains", kwargs={"pk": pk}))

    def _parse_domain_ids(self, domain_data, domain_type):
        """
        Parses the domain IDs from the request and handles JSON errors.
        """
        try:
            return json.loads(domain_data) if domain_data else []
        except json.JSONDecodeError:
            messages.error(
                self.request,
                f"Invalid data for {domain_type}. If the issue persists, "
                f"please contact {DefaultUserValues.HELP_EMAIL}.",
            )
            logger.error(f"Invalid data for {domain_type}.")
            return None

    def _process_added_domains(self, added_domain_ids, email, requestor, portfolio):
        """
        Processes added domain invitations by updating existing invitations
        or creating new ones.
        """
        if added_domain_ids:
            # get added_domains from ids to pass to send email method and bulk create
            added_domains = Domain.objects.filter(id__in=added_domain_ids)
            member_of_a_different_org, _ = get_org_membership(portfolio, email, None)
            send_domain_invitation_email(
                email=email,
                requestor=requestor,
                domains=added_domains,
                is_member_of_different_org=member_of_a_different_org,
            )

            # Update existing invitations from CANCELED to INVITED
            existing_invitations = DomainInvitation.objects.filter(domain__in=added_domains, email=email)
            existing_invitations.update(status=DomainInvitation.DomainInvitationStatus.INVITED)

            # Determine which domains need new invitations
            existing_domain_ids = existing_invitations.values_list("domain_id", flat=True)
            new_domain_ids = set(added_domain_ids) - set(existing_domain_ids)

            # Bulk create new invitations
            DomainInvitation.objects.bulk_create(
                [
                    DomainInvitation(
                        domain_id=domain_id,
                        email=email,
                        status=DomainInvitation.DomainInvitationStatus.INVITED,
                    )
                    for domain_id in new_domain_ids
                ]
            )

    def _process_removed_domains(self, removed_domain_ids, email):
        """
        Processes removed domain invitations by updating their status to CANCELED.
        """
        if not removed_domain_ids:
            return

        # Update invitations from INVITED to CANCELED
        DomainInvitation.objects.filter(
            domain_id__in=removed_domain_ids,
            email=email,
            status=DomainInvitation.DomainInvitationStatus.INVITED,
        ).update(status=DomainInvitation.DomainInvitationStatus.CANCELED)


class PortfolioNoDomainsView(NoPortfolioDomainsPermissionView, View):
    """Some users have access to the underlying portfolio, but not any domains.
    This is a custom view which explains that to the user - and denotes who to contact.
    """

    model = Portfolio
    template_name = "portfolio_no_domains.html"

    def get(self, request):
        return render(request, self.template_name, context=self.get_context_data())

    def get_context_data(self, **kwargs):
        """Add additional context data to the template."""
        # We can override the base class. This view only needs this item.
        context = {}
        portfolio = self.request.session.get("portfolio")
        if portfolio:
            admin_ids = UserPortfolioPermission.objects.filter(
                portfolio=portfolio,
                roles__overlap=[
                    UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
                ],
            ).values_list("user__id", flat=True)

            admin_users = User.objects.filter(id__in=admin_ids)
            context["portfolio_administrators"] = admin_users
        return context


class PortfolioNoDomainRequestsView(NoPortfolioDomainsPermissionView, View):
    """Some users have access to the underlying portfolio, but not any domain requests.
    This is a custom view which explains that to the user - and denotes who to contact.
    """

    model = Portfolio
    template_name = "portfolio_no_requests.html"

    def get(self, request):
        return render(request, self.template_name, context=self.get_context_data())

    def get_context_data(self, **kwargs):
        """Add additional context data to the template."""
        # We can override the base class. This view only needs this item.
        context = {}
        portfolio = self.request.session.get("portfolio")
        if portfolio:
            admin_ids = UserPortfolioPermission.objects.filter(
                portfolio=portfolio,
                roles__overlap=[
                    UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
                ],
            ).values_list("user__id", flat=True)

            admin_users = User.objects.filter(id__in=admin_ids)
            context["portfolio_administrators"] = admin_users
        return context


class PortfolioOrganizationView(PortfolioBasePermissionView, FormMixin):
    """
    View to handle displaying and updating the portfolio's organization details.
    """

    model = Portfolio
    template_name = "portfolio_organization.html"
    form_class = portfolioForms.PortfolioOrgAddressForm
    context_object_name = "portfolio"

    def get_context_data(self, **kwargs):
        """Add additional context data to the template."""
        context = super().get_context_data(**kwargs)
        portfolio = self.request.session.get("portfolio")
        context["has_edit_org_portfolio_permission"] = self.request.user.has_edit_org_portfolio_permission(portfolio)
        return context

    def get_object(self, queryset=None):
        """Get the portfolio object based on the session."""
        portfolio = self.request.session.get("portfolio")
        if portfolio is None:
            raise Http404("No organization found for this user")
        return portfolio

    def get_form_kwargs(self):
        """Include the instance in the form kwargs."""
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object()
        return kwargs

    def get(self, request, *args, **kwargs):
        """Handle GET requests to display the form."""
        self.object = self.get_object()
        form = self.get_form()
        return self.render_to_response(self.get_context_data(form=form))

    def post(self, request, *args, **kwargs):
        """Handle POST requests to process form submission."""
        self.object = self.get_object()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def form_valid(self, form):
        """Handle the case when the form is valid."""
        self.object = form.save(commit=False)
        self.object.creator = self.request.user
        self.object.save()
        messages.success(self.request, "The organization information for this portfolio has been updated.")
        return super().form_valid(form)

    def form_invalid(self, form):
        """Handle the case when the form is invalid."""
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        """Redirect to the overview page for the portfolio."""
        return reverse("organization")


class PortfolioSeniorOfficialView(PortfolioBasePermissionView, FormMixin):
    """
    View to handle displaying and updating the portfolio's senior official details.
    For now, this view is readonly.
    """

    model = Portfolio
    template_name = "portfolio_senior_official.html"
    form_class = portfolioForms.PortfolioSeniorOfficialForm
    context_object_name = "portfolio"

    def get_object(self, queryset=None):
        """Get the portfolio object based on the session."""
        portfolio = self.request.session.get("portfolio")
        if portfolio is None:
            raise Http404("No organization found for this user")
        return portfolio

    def get_form_kwargs(self):
        """Include the instance in the form kwargs."""
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object().senior_official
        return kwargs

    def get(self, request, *args, **kwargs):
        """Handle GET requests to display the form."""
        self.object = self.get_object()
        form = self.get_form()
        return self.render_to_response(self.get_context_data(form=form))


class PortfolioMembersView(PortfolioMembersPermissionView, View):

    template_name = "portfolio_members.html"

    def get(self, request):
        """Add additional context data to the template."""
        return render(request, "portfolio_members.html")


class PortfolioAddMemberView(PortfolioMembersPermissionView, FormMixin):

    template_name = "portfolio_members_add_new.html"
    form_class = portfolioForms.PortfolioNewMemberForm

    def get(self, request, *args, **kwargs):
        """Handle GET requests to display the form."""
        self.object = None  # No existing PortfolioInvitation instance
        form = self.get_form()
        return self.render_to_response(self.get_context_data(form=form))

    def post(self, request, *args, **kwargs):
        """Handle POST requests to process form submission."""
        self.object = None  # For a new invitation, there's no existing model instance

        # portfolio not submitted with form, so override the value
        data = request.POST.copy()
        if not data.get("portfolio"):
            data["portfolio"] = self.request.session.get("portfolio").id
        # Pass the modified data to the form
        form = portfolioForms.PortfolioNewMemberForm(data)

        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def is_ajax(self):
        return self.request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def form_invalid(self, form):
        if self.is_ajax():
            return JsonResponse({"is_valid": False})  # Return a JSON response
        else:
            return super().form_invalid(form)  # Handle non-AJAX requests normally

    def form_valid(self, form):
        super().form_valid(form)
        if self.is_ajax():
            return JsonResponse({"is_valid": True})  # Return a JSON response
        else:
            return self.submit_new_member(form)

    def get_success_url(self):
        """Redirect to members table."""
        return reverse("members")

    def submit_new_member(self, form):
        """Add the specified user as a member for this portfolio."""
        requested_email = form.cleaned_data["email"]
        requestor = self.request.user
        portfolio = form.cleaned_data["portfolio"]

        requested_user = User.objects.filter(email=requested_email).first()
        permission_exists = UserPortfolioPermission.objects.filter(user=requested_user, portfolio=portfolio).exists()
        try:
            if not requested_user or not permission_exists:
                send_portfolio_invitation_email(email=requested_email, requestor=requestor, portfolio=portfolio)
                portfolio_invitation = form.save()
                # if user exists for email, immediately retrieve portfolio invitation upon creation
                if requested_user is not None:
                    portfolio_invitation.retrieve()
                    portfolio_invitation.save()
                messages.success(self.request, f"{requested_email} has been invited.")
            else:
                if permission_exists:
                    messages.warning(self.request, "User is already a member of this portfolio.")
        except Exception as e:
            self._handle_exceptions(e, portfolio, requested_email)
        return redirect(self.get_success_url())

    def _handle_exceptions(self, exception, portfolio, email):
        """Handle exceptions raised during the process."""
        if isinstance(exception, EmailSendingError):
            logger.warning(
                "Could not sent email invitation to %s for portfolio %s (EmailSendingError)",
                email,
                portfolio,
                exc_info=True,
            )
            messages.warning(self.request, "Could not send email invitation.")
        elif isinstance(exception, MissingEmailError):
            messages.error(self.request, str(exception))
            logger.error(
                f"Can't send email to '{email}' for portfolio '{portfolio}'. No email exists for the requestor.",
                exc_info=True,
            )
        else:
            logger.warning("Could not send email invitation (Other Exception)", exc_info=True)
            messages.warning(self.request, "Could not send email invitation.")
