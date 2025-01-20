from datetime import date
from django.conf import settings
from registrar.models import Domain, DomainInvitation, UserDomainRole
from registrar.utility.errors import (
    AlreadyDomainInvitedError,
    AlreadyDomainManagerError,
    MissingEmailError,
    OutsideOrgMemberError,
)
from registrar.utility.waffle import flag_is_active_for_user
from registrar.utility.email import EmailSendingError, send_templated_email
import logging

logger = logging.getLogger(__name__)


def send_domain_invitation_email(
    email: str, requestor, domains: Domain | list[Domain], is_member_of_different_org, requested_user=None
):
    """
    Sends a domain invitation email to the specified address.

    Args:
        email (str): Email address of the recipient.
        requestor (User): The user initiating the invitation.
        domains (Domain or list of Domain): The domain objects for which the invitation is being sent.
        is_member_of_different_org (bool): if an email belongs to a different org
        requested_user (User | None): The recipient if the email belongs to a user in the registrar

    Raises:
        MissingEmailError: If the requestor has no email associated with their account.
        AlreadyDomainManagerError: If the email corresponds to an existing domain manager.
        AlreadyDomainInvitedError: If an invitation has already been sent.
        OutsideOrgMemberError: If the requested_user is part of a different organization.
        EmailSendingError: If there is an error while sending the email.
    """
    domains = normalize_domains(domains)
    requestor_email = get_requestor_email(requestor, domains)

    validate_invitation(email, domains, requestor, is_member_of_different_org)

    send_invitation_email(email, requestor_email, domains, requested_user)

    # send emails to domain managers
    for domain in domains:
        send_emails_to_domain_managers(
            email=email,
            requestor_email=requestor_email,
            domain=domain,
            requested_user=requested_user,
        )


def send_emails_to_domain_managers(email: str, requestor_email, domain: Domain, requested_user=None):
    """
    Notifies all domain managers of the provided domain of a change
    Raises:
        EmailSendingError
    """
    # Get each domain manager from list
    user_domain_roles = UserDomainRole.objects.filter(domain=domain)
    for user_domain_role in user_domain_roles:
        # Send email to each domain manager
        user = user_domain_role.user
        try:
            send_templated_email(
                "emails/domain_manager_notification.txt",
                "emails/domain_manager_notification_subject.txt",
                to_address=user.email,
                context={
                    "domain": domain,
                    "requestor_email": requestor_email,
                    "invited_email_address": email,
                    "domain_manager": user,
                    "date": date.today(),
                },
            )
        except EmailSendingError as err:
            raise EmailSendingError(
                f"Could not send email manager notification to {user.email} for domain: {domain.name}"
            ) from err


def normalize_domains(domains: Domain | list[Domain]) -> list[Domain]:
    """Ensures domains is always a list."""
    return [domains] if isinstance(domains, Domain) else domains


def get_requestor_email(requestor, domains):
    """Get the requestor's email or raise an error if it's missing.

    If the requestor is staff, default email is returned.
    """
    if requestor.is_staff:
        return settings.DEFAULT_FROM_EMAIL

    if not requestor.email or requestor.email.strip() == "":
        domain_names = ", ".join([domain.name for domain in domains])
        raise MissingEmailError(email=requestor.email, domain=domain_names)

    return requestor.email


def validate_invitation(email, domains, requestor, is_member_of_different_org):
    """Validate the invitation conditions."""
    check_outside_org_membership(email, requestor, is_member_of_different_org)

    for domain in domains:
        validate_existing_invitation(email, domain)

        # NOTE: should we also be validating against existing user_domain_roles


def check_outside_org_membership(email, requestor, is_member_of_different_org):
    """Raise an error if the email belongs to a different organization."""
    if (
        flag_is_active_for_user(requestor, "organization_feature")
        and not flag_is_active_for_user(requestor, "multiple_portfolios")
        and is_member_of_different_org
    ):
        raise OutsideOrgMemberError(email=email)


def validate_existing_invitation(email, domain):
    """Check for existing invitations and handle their status."""
    try:
        invite = DomainInvitation.objects.get(email=email, domain=domain)
        if invite.status == DomainInvitation.DomainInvitationStatus.RETRIEVED:
            raise AlreadyDomainManagerError(email)
        elif invite.status == DomainInvitation.DomainInvitationStatus.CANCELED:
            invite.update_cancellation_status()
            invite.save()
        else:
            raise AlreadyDomainInvitedError(email)
    except DomainInvitation.DoesNotExist:
        pass


def send_invitation_email(email, requestor_email, domains, requested_user):
    """Send the invitation email."""
    try:
        send_templated_email(
            "emails/domain_invitation.txt",
            "emails/domain_invitation_subject.txt",
            to_address=email,
            context={
                "domains": domains,
                "requestor_email": requestor_email,
                "invitee_email_address": email,
                "requested_user": requested_user,
            },
        )
    except EmailSendingError as err:
        domain_names = ", ".join([domain.name for domain in domains])
        raise EmailSendingError(f"Could not send email invitation to {email} for domains: {domain_names}") from err


def send_portfolio_invitation_email(email: str, requestor, portfolio):
    """
    Sends a portfolio member invitation email to the specified address.

    Raises exceptions for validation or email-sending issues.

    Args:
        email (str): Email address of the recipient
        requestor (User): The user initiating the invitation.
        portfolio (Portfolio): The portfolio object for which the invitation is being sent.

    Raises:
        MissingEmailError: If the requestor has no email associated with their account.
        EmailSendingError: If there is an error while sending the email.
    """

    # Default email address for staff
    requestor_email = settings.DEFAULT_FROM_EMAIL

    # Check if the requestor is staff and has an email
    if not requestor.is_staff:
        if not requestor.email or requestor.email.strip() == "":
            raise MissingEmailError(email=email, portfolio=portfolio)
        else:
            requestor_email = requestor.email

    try:
        send_templated_email(
            "emails/portfolio_invitation.txt",
            "emails/portfolio_invitation_subject.txt",
            to_address=email,
            context={
                "portfolio": portfolio,
                "requestor_email": requestor_email,
                "email": email,
            },
        )
    except EmailSendingError as err:
        raise EmailSendingError(
            f"Could not sent email invitation to {email} for portfolio {portfolio}. Portfolio invitation not saved."
        ) from err
