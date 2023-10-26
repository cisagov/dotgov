import logging

from django.contrib.auth.models import AbstractUser
from django.db import models

from .domain_invitation import DomainInvitation
from .transition_domain import TransitionDomain
from .domain_information import DomainInformation
from .domain import Domain

from phonenumber_field.modelfields import PhoneNumberField  # type: ignore


logger = logging.getLogger(__name__)


class User(AbstractUser):
    """
    A custom user model that performs identically to the default user model
    but can be customized later.
    """

    # #### Constants for choice fields ####
    RESTRICTED = "restricted"
    STATUS_CHOICES = ((RESTRICTED, RESTRICTED),)

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=None,  # Set the default value to None
        null=True,  # Allow the field to be null
        blank=True,  # Allow the field to be blank
    )

    domains = models.ManyToManyField(
        "registrar.Domain",
        through="registrar.UserDomainRole",
        related_name="users",
    )

    phone = PhoneNumberField(
        null=True,
        blank=True,
        help_text="Phone",
        db_index=True,
    )

    def __str__(self):
        # this info is pulled from Login.gov
        if self.first_name or self.last_name:
            return f"{self.first_name or ''} {self.last_name or ''} {self.email or ''}"
        elif self.email:
            return self.email
        else:
            return self.username

    def restrict_user(self):
        self.status = self.RESTRICTED
        self.save()

    def unrestrict_user(self):
        self.status = None
        self.save()

    def is_restricted(self):
        return self.status == self.RESTRICTED

    def check_domain_invitations_on_login(self):
        """When a user first arrives on the site, we need to retrieve any domain
        invitations that match their email address."""
        for invitation in DomainInvitation.objects.filter(
            email=self.email, status=DomainInvitation.INVITED
        ):
            try:
                invitation.retrieve()
                invitation.save()
            except RuntimeError:
                # retrieving should not fail because of a missing user, but
                # if it does fail, log the error so a new user can continue
                # logging in
                logger.warn(
                    "Failed to retrieve invitation %s", invitation, exc_info=True
                )

    def create_domain_and_invite(self, transition_domain: TransitionDomain):
        transition_domain_name = transition_domain.domain_name
        transition_domain_status = transition_domain.status
        transition_domain_email = transition_domain.username

        # type safety check.  name should never be none
        if transition_domain_name is not None:
            new_domain = Domain(
                name=transition_domain_name, state=transition_domain_status
            )
            new_domain.save()
            # check that a domain invitation doesn't already
            # exist for this e-mail / Domain pair
            domain_email_already_in_domain_invites = DomainInvitation.objects.filter(
                email=transition_domain_email.lower(), domain=new_domain
            ).exists()
            if not domain_email_already_in_domain_invites:
                # Create new domain invitation
                new_domain_invitation = DomainInvitation(
                    email=transition_domain_email.lower(), domain=new_domain
                )
                new_domain_invitation.save()

    def check_transition_domains_on_login(self):
        """When a user first arrives on the site, we need to check
        if they are logging in with the same e-mail as a
        transition domain and update our database accordingly."""

        for transition_domain in TransitionDomain.objects.filter(username=self.email):
            # Looks like the user logged in with the same e-mail as
            # one or more corresponding transition domains.
            # Create corresponding DomainInformation objects.

            # NOTE: adding an ADMIN user role for this user
            # for each domain should already be done
            # in the invitation.retrieve() method.
            # However, if the migration scripts for transition
            # domain objects were not executed correctly,
            # there could be transition domains without
            # any corresponding Domain & DomainInvitation objects,
            # which means the invitation.retrieve() method might
            # not execute.
            # Check that there is a corresponding domain object
            # for this transition domain.  If not, we have an error
            # with our data and migrations need to be run again.

            # Get the domain that corresponds with this transition domain
            domain_exists = Domain.objects.filter(
                name=transition_domain.domain_name
            ).exists()
            if not domain_exists:
                logger.warn(
                    """There are transition domains without
                            corresponding domain objects!
                            Please run migration scripts for transition domains
                            (See data_migration.md)"""
                )
                # No need to throw an exception...just create a domain
                # and domain invite, then proceed as normal
                self.create_domain_and_invite(transition_domain)

            domain = Domain.objects.get(name=transition_domain.domain_name)

            # Create a domain information object, if one doesn't
            # already exist
            domain_info_exists = DomainInformation.objects.filter(
                domain=domain
            ).exists()
            if not domain_info_exists:
                new_domain_info = DomainInformation(creator=self, domain=domain)
                new_domain_info.save()

    def first_login(self):
        """Callback when the user is authenticated for the very first time.

        When a user first arrives on the site, we need to retrieve any domain
        invitations that match their email address.

        We also need to check if they are logging in with the same e-mail
        as a transition domain and update our domainInfo objects accordingly.
        """

        # PART 1: TRANSITION DOMAINS
        #
        # NOTE: THIS MUST RUN FIRST
        # (If we have an issue where transition domains were
        # not fully converted into Domain and DomainInvitation
        # objects, this method will fill in the gaps.
        # This will ensure the Domain Invitations method
        # runs correctly (no missing invites))
        self.check_transition_domains_on_login()

        # PART 2: DOMAIN INVITATIONS
        self.check_domain_invitations_on_login()

    class Meta:
        permissions = [
            ("analyst_access_permission", "Analyst Access Permission"),
            ("full_access_permission", "Full Access Permission"),
        ]
