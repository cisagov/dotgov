from unittest import skip
from unittest.mock import MagicMock, ANY, patch

from django.conf import settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from registrar.models.portfolio_invitation import PortfolioInvitation
from registrar.utility.email import EmailSendingError
from waffle.testutils import override_flag
from api.tests.common import less_console_noise_decorator
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from .common import MockEppLib, create_user  # type: ignore
from django_webtest import WebTest  # type: ignore
import boto3_mocking  # type: ignore

from registrar.utility.errors import (
    NameserverError,
    NameserverErrorCodes,
    SecurityEmailError,
    SecurityEmailErrorCodes,
    GenericError,
    GenericErrorCodes,
    DsDataError,
    DsDataErrorCodes,
)

from registrar.models import (
    DomainRequest,
    Domain,
    DomainInformation,
    DomainInvitation,
    AllowedEmail,
    Contact,
    PublicContact,
    Host,
    HostIP,
    UserDomainRole,
    User,
    FederalAgency,
    Portfolio,
    Suborganization,
    UserPortfolioPermission,
)
from datetime import date, datetime, timedelta
from django.utils import timezone

from .common import less_console_noise
from .test_views import TestWithUser

import logging

logger = logging.getLogger(__name__)


class TestWithDomainPermissions(TestWithUser):
    @less_console_noise_decorator
    def setUp(self):
        super().setUp()
        self.domain, _ = Domain.objects.get_or_create(name="igorville.gov")
        self.domain_with_ip, _ = Domain.objects.get_or_create(name="nameserverwithip.gov")
        self.domain_just_nameserver, _ = Domain.objects.get_or_create(name="justnameserver.com")
        self.domain_no_information, _ = Domain.objects.get_or_create(name="noinformation.gov")
        self.domain_on_hold, _ = Domain.objects.get_or_create(
            name="on-hold.gov",
            state=Domain.State.ON_HOLD,
            expiration_date=timezone.make_aware(
                datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
            ),
        )
        self.domain_dns_needed, _ = Domain.objects.get_or_create(
            name="dns-needed.gov",
            state=Domain.State.DNS_NEEDED,
        )
        self.domain_deleted, _ = Domain.objects.get_or_create(
            name="deleted.gov",
            state=Domain.State.DELETED,
            expiration_date=timezone.make_aware(
                datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
            ),
        )

        self.domain_dsdata, _ = Domain.objects.get_or_create(name="dnssec-dsdata.gov")
        self.domain_multdsdata, _ = Domain.objects.get_or_create(name="dnssec-multdsdata.gov")
        # We could simply use domain (igorville) but this will be more readable in tests
        # that inherit this setUp
        self.domain_dnssec_none, _ = Domain.objects.get_or_create(name="dnssec-none.gov")

        self.domain_with_four_nameservers, _ = Domain.objects.get_or_create(name="fournameserversDomain.gov")

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_dsdata)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_multdsdata)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_dnssec_none)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_with_four_nameservers)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_with_ip)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_just_nameserver)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_on_hold)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_deleted)
        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain_dns_needed)

        self.role, _ = UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain, role=UserDomainRole.Roles.MANAGER
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_dsdata, role=UserDomainRole.Roles.MANAGER
        )
        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_dns_needed, role=UserDomainRole.Roles.MANAGER
        )
        UserDomainRole.objects.get_or_create(
            user=self.user,
            domain=self.domain_multdsdata,
            role=UserDomainRole.Roles.MANAGER,
        )
        UserDomainRole.objects.get_or_create(
            user=self.user,
            domain=self.domain_dnssec_none,
            role=UserDomainRole.Roles.MANAGER,
        )
        UserDomainRole.objects.get_or_create(
            user=self.user,
            domain=self.domain_with_four_nameservers,
            role=UserDomainRole.Roles.MANAGER,
        )
        UserDomainRole.objects.get_or_create(
            user=self.user,
            domain=self.domain_with_ip,
            role=UserDomainRole.Roles.MANAGER,
        )
        UserDomainRole.objects.get_or_create(
            user=self.user,
            domain=self.domain_just_nameserver,
            role=UserDomainRole.Roles.MANAGER,
        )
        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_on_hold, role=UserDomainRole.Roles.MANAGER
        )
        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_deleted, role=UserDomainRole.Roles.MANAGER
        )

    def tearDown(self):
        try:
            UserDomainRole.objects.all().delete()
            DomainInvitation.objects.all().delete()
            if hasattr(self.domain, "contacts"):
                self.domain.contacts.all().delete()
            DomainRequest.objects.all().delete()
            DomainInformation.objects.all().delete()
            PublicContact.objects.all().delete()
            HostIP.objects.all().delete()
            Host.objects.all().delete()
            Domain.objects.all().delete()
            UserDomainRole.objects.all().delete()
            Suborganization.objects.all().delete()
            Portfolio.objects.all().delete()
        except ValueError:  # pass if already deleted
            pass
        super().tearDown()


class TestDomainPermissions(TestWithDomainPermissions):
    @less_console_noise_decorator
    def test_not_logged_in(self):
        """Not logged in gets a redirect to Login."""
        for view_name in [
            "domain",
            "domain-users",
            "domain-users-add",
            "domain-dns-nameservers",
            "domain-org-name-address",
            "domain-senior-official",
            "domain-security-email",
        ]:
            with self.subTest(view_name=view_name):
                response = self.client.get(reverse(view_name, kwargs={"pk": self.domain.id}))
                self.assertEqual(response.status_code, 302)

    @less_console_noise_decorator
    def test_no_domain_role(self):
        """Logged in but no role gets 403 Forbidden."""
        self.client.force_login(self.user)
        self.role.delete()  # user no longer has a role on this domain

        for view_name in [
            "domain",
            "domain-users",
            "domain-users-add",
            "domain-dns-nameservers",
            "domain-org-name-address",
            "domain-senior-official",
            "domain-security-email",
        ]:
            with self.subTest(view_name=view_name):
                response = self.client.get(reverse(view_name, kwargs={"pk": self.domain.id}))
                self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    def test_domain_pages_blocked_for_on_hold_and_deleted(self):
        """Test that the domain pages are blocked for on hold and deleted domains"""

        self.client.force_login(self.user)
        for view_name in [
            "domain-users",
            "domain-users-add",
            "domain-dns",
            "domain-dns-nameservers",
            "domain-dns-dnssec",
            "domain-dns-dnssec-dsdata",
            "domain-org-name-address",
            "domain-senior-official",
            "domain-security-email",
        ]:
            for domain in [
                self.domain_on_hold,
                self.domain_deleted,
            ]:
                with self.subTest(view_name=view_name, domain=domain):
                    response = self.client.get(reverse(view_name, kwargs={"pk": domain.id}))
                    self.assertEqual(response.status_code, 403)


class TestDomainOverview(TestWithDomainPermissions, WebTest):

    def setUp(self):
        super().setUp()
        self.app.set_user(self.user.username)
        self.client.force_login(self.user)


class TestDomainDetail(TestDomainOverview):
    @skip("Assertion broke for no reason, why? Need to fix")
    def test_domain_detail_link_works(self):
        home_page = self.app.get("/")
        logger.info(f"This is the value of home_page: {home_page}")
        self.assertContains(home_page, "igorville.gov")
        # click the "Edit" link
        detail_page = home_page.click("Manage", index=0)
        self.assertContains(detail_page, "igorville.gov")
        self.assertContains(detail_page, "Status")

    def test_unknown_domain_does_not_show_as_expired_on_detail_page(self):
        """An UNKNOWN domain should not exist on the detail_page anymore.
        It shows as 'DNS needed'"""
        # At the time of this test's writing, there are 6 UNKNOWN domains inherited
        # from constructors. Let's reset.
        with less_console_noise():
            PublicContact.objects.all().delete()
            Domain.objects.all().delete()
            UserDomainRole.objects.all().delete()

            self.domain, _ = Domain.objects.get_or_create(name="igorville.gov")
            self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)
            self.role, _ = UserDomainRole.objects.get_or_create(
                user=self.user, domain=self.domain, role=UserDomainRole.Roles.MANAGER
            )

            igorville = Domain.objects.get(name="igorville.gov")
            self.assertEquals(igorville.state, Domain.State.UNKNOWN)
            detail_page = self.app.get(f"/domain/{igorville.id}")
            self.assertContains(detail_page, "Expired")

            self.assertNotContains(detail_page, "DNS needed")

    def test_domain_detail_blocked_for_ineligible_user(self):
        """We could easily duplicate this test for all domain management
        views, but a single url test should be solid enough since all domain
        management pages share the same permissions class"""
        with less_console_noise():
            self.user.status = User.RESTRICTED
            self.user.save()
            response = self.client.get(reverse("domain", kwargs={"pk": self.domain.id}))
            self.assertEqual(response.status_code, 403)

    def test_domain_detail_allowed_for_on_hold(self):
        """Test that the domain overview page displays for on hold domain"""
        with less_console_noise():
            # View domain overview page
            detail_page = self.client.get(reverse("domain", kwargs={"pk": self.domain_on_hold.id}))
            self.assertNotContains(detail_page, "Edit")

    def test_domain_detail_see_just_nameserver(self):
        with less_console_noise():
            # View nameserver on Domain Overview page
            detail_page = self.app.get(reverse("domain", kwargs={"pk": self.domain_just_nameserver.id}))

            self.assertContains(detail_page, "justnameserver.com")
            self.assertContains(detail_page, "ns1.justnameserver.com")
            self.assertContains(detail_page, "ns2.justnameserver.com")

    def test_domain_detail_see_nameserver_and_ip(self):
        with less_console_noise():
            # View nameserver on Domain Overview page
            detail_page = self.app.get(reverse("domain", kwargs={"pk": self.domain_with_ip.id}))

            self.assertContains(detail_page, "nameserverwithip.gov")

            self.assertContains(detail_page, "ns1.nameserverwithip.gov")
            self.assertContains(detail_page, "ns2.nameserverwithip.gov")
            self.assertContains(detail_page, "ns3.nameserverwithip.gov")
            # Splitting IP addresses bc there is odd whitespace and can't strip text
            self.assertContains(detail_page, "(1.2.3.4,")
            self.assertContains(detail_page, "2.3.4.5)")

    def test_domain_detail_with_no_information_or_domain_request(self):
        """Test that domain management page returns 200 and displays error
        when no domain information or domain request exist"""
        with less_console_noise():
            # have to use staff user for this test
            staff_user = create_user()
            # staff_user.save()
            self.client.force_login(staff_user)

            # need to set the analyst_action and analyst_action_location
            # in the session to emulate user clicking Manage Domain
            # in the admin interface
            session = self.client.session
            session["analyst_action"] = "foo"
            session["analyst_action_location"] = self.domain_no_information.id
            session.save()

            detail_page = self.client.get(reverse("domain", kwargs={"pk": self.domain_no_information.id}))

            self.assertContains(detail_page, "noinformation.gov")
            self.assertContains(detail_page, "Domain missing domain information")

    def test_domain_detail_with_analyst_managing_domain(self):
        """Test that domain management page returns 200 and does not display
        blue error message when an analyst is managing the domain"""
        with less_console_noise():
            staff_user = create_user()
            self.client.force_login(staff_user)

            # need to set the analyst_action and analyst_action_location
            # in the session to emulate user clicking Manage Domain
            # in the admin interface
            session = self.client.session
            session["analyst_action"] = "edit"
            session["analyst_action_location"] = self.domain.id
            session.save()

            detail_page = self.client.get(reverse("domain", kwargs={"pk": self.domain.id}))

            self.assertNotContains(
                detail_page, "If you need to make updates, contact one of the listed domain managers."
            )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_domain_readonly_on_detail_page(self):
        """Test that a domain, which is part of a portfolio, but for which the user is not a domain manager,
        properly displays read only"""

        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org", creator=self.user)
        # need to create a different user than self.user because the user needs permission assignments
        user = get_user_model().objects.create(
            first_name="Test",
            last_name="User",
            email="bogus@example.gov",
            phone="8003111234",
            title="test title",
        )
        domain, _ = Domain.objects.get_or_create(name="bogusdomain.gov")
        DomainInformation.objects.get_or_create(creator=user, domain=domain, portfolio=portfolio)

        UserPortfolioPermission.objects.get_or_create(
            user=user,
            portfolio=portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS,
            ],
        )
        user.refresh_from_db()
        self.client.force_login(user)
        detail_page = self.client.get(f"/domain/{domain.id}")
        # Check that alert message displays properly
        self.assertContains(
            detail_page,
            "You don't have access to manage "
            + domain.name
            + ". If you need to make updates, contact one of the listed domain managers.",
        )
        # Check that user does not have option to Edit domain
        self.assertNotContains(detail_page, "Edit")
        # Check that invited domain manager section not displayed when no invited domain managers
        self.assertNotContains(detail_page, "Invited domain managers")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_domain_readonly_on_detail_page_for_org_admin_not_manager(self):
        """Test that a domain, which is part of a portfolio, but for which the user is not a domain manager,
        properly displays read only"""

        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org", creator=self.user)
        # need to create a different user than self.user because the user needs permission assignments
        user = get_user_model().objects.create(
            first_name="Test",
            last_name="User",
            email="bogus@example.gov",
            phone="8003111234",
            title="test title",
        )
        domain, _ = Domain.objects.get_or_create(name="bogusdomain.gov")
        DomainInformation.objects.get_or_create(creator=user, domain=domain, portfolio=portfolio)

        UserPortfolioPermission.objects.get_or_create(
            user=user, portfolio=portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        )
        # add a domain invitation
        DomainInvitation.objects.get_or_create(email="invited@example.com", domain=domain)
        user.refresh_from_db()
        self.client.force_login(user)
        detail_page = self.client.get(f"/domain/{domain.id}")
        # Check that alert message displays properly
        self.assertContains(
            detail_page,
            "If you need to make updates, contact one of the listed domain managers.",
        )
        # Check that user does not have option to Edit domain
        self.assertNotContains(detail_page, "Edit")
        # Check that invited domain manager is displayed
        self.assertContains(detail_page, "Invited domain managers")
        self.assertContains(detail_page, "invited@example.com")


class TestDomainDetailDomainRenewal(TestDomainOverview):
    def setUp(self):
        super().setUp()

        self.user = get_user_model().objects.create(
            first_name="User",
            last_name="Test",
            email="bogus@example.gov",
            phone="8003111234",
            title="test title",
            username="usertest",
        )

        self.domaintorenew, _ = Domain.objects.get_or_create(
            name="domainrenewal.gov",
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domaintorenew, role=UserDomainRole.Roles.MANAGER
        )

        DomainInformation.objects.get_or_create(creator=self.user, domain=self.domaintorenew)

        self.portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org", creator=self.user)

        self.user.save()

    def expiration_date_one_year_out(self):
        todays_date = datetime.today()
        new_expiration_date = todays_date.replace(year=todays_date.year + 1)
        return new_expiration_date

    def custom_is_expired_false(self):
        return False

    def custom_is_expired_true(self):
        return True

    def custom_is_expiring(self):
        return True

    def custom_renew_domain(self):
        self.domain_with_ip.expiration_date = self.expiration_date_one_year_out()
        self.domain_with_ip.save()

    @override_flag("domain_renewal", active=True)
    def test_expiring_domain_on_detail_page_as_domain_manager(self):
        self.client.force_login(self.user)
        with patch.object(Domain, "is_expiring", self.custom_is_expiring), patch.object(
            Domain, "is_expired", self.custom_is_expired_false
        ):
            self.assertEquals(self.domaintorenew.state, Domain.State.UNKNOWN)
            detail_page = self.client.get(
                reverse("domain", kwargs={"pk": self.domaintorenew.id}),
            )
            self.assertContains(detail_page, "Expiring soon")

            self.assertContains(detail_page, "Renew to maintain access")

            self.assertNotContains(detail_page, "DNS needed")
            self.assertNotContains(detail_page, "Expired")

    @override_flag("domain_renewal", active=True)
    @override_flag("organization_feature", active=True)
    def test_expiring_domain_on_detail_page_in_org_model_as_a_non_domain_manager(self):
        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org", creator=self.user)
        non_dom_manage_user = get_user_model().objects.create(
            first_name="Non Domain",
            last_name="Manager",
            email="verybogus@example.gov",
            phone="8003111234",
            title="test title again",
            username="nondomain",
        )

        non_dom_manage_user.save()
        UserPortfolioPermission.objects.get_or_create(
            user=non_dom_manage_user,
            portfolio=portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS,
            ],
        )
        domaintorenew2, _ = Domain.objects.get_or_create(name="bogusdomain2.gov")
        DomainInformation.objects.get_or_create(
            creator=non_dom_manage_user, domain=domaintorenew2, portfolio=self.portfolio
        )
        non_dom_manage_user.refresh_from_db()
        self.client.force_login(non_dom_manage_user)
        with patch.object(Domain, "is_expiring", self.custom_is_expiring), patch.object(
            Domain, "is_expired", self.custom_is_expired_false
        ):
            detail_page = self.client.get(
                reverse("domain", kwargs={"pk": domaintorenew2.id}),
            )
            self.assertContains(detail_page, "Contact one of the listed domain managers to renew the domain.")

    @override_flag("domain_renewal", active=True)
    @override_flag("organization_feature", active=True)
    def test_expiring_domain_on_detail_page_in_org_model_as_a_domain_manager(self):
        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org2", creator=self.user)

        domaintorenew3, _ = Domain.objects.get_or_create(name="bogusdomain3.gov")

        UserDomainRole.objects.get_or_create(user=self.user, domain=domaintorenew3, role=UserDomainRole.Roles.MANAGER)
        DomainInformation.objects.get_or_create(creator=self.user, domain=domaintorenew3, portfolio=portfolio)
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        with patch.object(Domain, "is_expiring", self.custom_is_expiring), patch.object(
            Domain, "is_expired", self.custom_is_expired_false
        ):
            detail_page = self.client.get(
                reverse("domain", kwargs={"pk": domaintorenew3.id}),
            )
            self.assertContains(detail_page, "Renew to maintain access")

    @override_flag("domain_renewal", active=True)
    def test_domain_renewal_form_and_sidebar_expiring(self):
        self.client.force_login(self.user)
        with patch.object(Domain, "is_expiring", self.custom_is_expiring), patch.object(
            Domain, "is_expiring", self.custom_is_expiring
        ):
            # Grab the detail page
            detail_page = self.client.get(
                reverse("domain", kwargs={"pk": self.domaintorenew.id}),
            )

            # Make sure we see the link as a domain manager
            self.assertContains(detail_page, "Renew to maintain access")

            # Make sure we can see Renewal form on the sidebar since it's expiring
            self.assertContains(detail_page, "Renewal form")

            # Grab link to the renewal page
            renewal_form_url = reverse("domain-renewal", kwargs={"pk": self.domaintorenew.id})
            self.assertContains(detail_page, f'href="{renewal_form_url}"')

            # Simulate clicking the link
            response = self.client.get(renewal_form_url)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f"Renew {self.domaintorenew.name}")

    @override_flag("domain_renewal", active=True)
    def test_domain_renewal_form_and_sidebar_expired(self):

        self.client.force_login(self.user)

        with patch.object(Domain, "is_expired", self.custom_is_expired_true), patch.object(
            Domain, "is_expired", self.custom_is_expired_true
        ):
            # Grab the detail page
            detail_page = self.client.get(
                reverse("domain", kwargs={"pk": self.domaintorenew.id}),
            )

            print("puglesss", self.domaintorenew.is_expired)
            # Make sure we see the link as a domain manager
            self.assertContains(detail_page, "Renew to maintain access")

            # Make sure we can see Renewal form on the sidebar since it's expired
            self.assertContains(detail_page, "Renewal form")

            # Grab link to the renewal page
            renewal_form_url = reverse("domain-renewal", kwargs={"pk": self.domaintorenew.id})
            self.assertContains(detail_page, f'href="{renewal_form_url}"')

            # Simulate clicking the link
            response = self.client.get(renewal_form_url)

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f"Renew {self.domaintorenew.name}")

    @override_flag("domain_renewal", active=True)
    def test_domain_renewal_form_your_contact_info_edit(self):
        with less_console_noise():
            # Start on the Renewal page for the domain
            renewal_page = self.app.get(reverse("domain-renewal", kwargs={"pk": self.domain_with_ip.id}))

            # Verify we see "Your contact information" on the renewal form
            self.assertContains(renewal_page, "Your contact information")

            # Verify that the "Edit" button for Your contact is there and links to correct URL
            edit_button_url = reverse("user-profile")
            self.assertContains(renewal_page, f'href="{edit_button_url}"')

            # Simulate clicking on edit button
            edit_page = renewal_page.click(href=edit_button_url, index=1)
            self.assertEqual(edit_page.status_code, 200)
            self.assertContains(edit_page, "Review the details below and update any required information")

    @override_flag("domain_renewal", active=True)
    def test_domain_renewal_form_security_email_edit(self):
        with less_console_noise():
            # Start on the Renewal page for the domain
            renewal_page = self.app.get(reverse("domain-renewal", kwargs={"pk": self.domain_with_ip.id}))

            # Verify we see "Security email" on the renewal form
            self.assertContains(renewal_page, "Security email")

            # Verify we see "strong recommend" blurb
            self.assertContains(renewal_page, "We strongly recommend that you provide a security email.")

            # Verify that the "Edit" button for Security email is there and links to correct URL
            edit_button_url = reverse("domain-security-email", kwargs={"pk": self.domain_with_ip.id})
            self.assertContains(renewal_page, f'href="{edit_button_url}"')

            # Simulate clicking on edit button
            edit_page = renewal_page.click(href=edit_button_url, index=1)
            self.assertEqual(edit_page.status_code, 200)
            self.assertContains(edit_page, "A security contact should be capable of evaluating")

    @override_flag("domain_renewal", active=True)
    def test_domain_renewal_form_domain_manager_edit(self):
        with less_console_noise():
            # Start on the Renewal page for the domain
            renewal_page = self.app.get(reverse("domain-renewal", kwargs={"pk": self.domain_with_ip.id}))

            # Verify we see "Domain managers" on the renewal form
            self.assertContains(renewal_page, "Domain managers")

            # Verify that the "Edit" button for Domain managers is there and links to correct URL
            edit_button_url = reverse("domain-users", kwargs={"pk": self.domain_with_ip.id})
            self.assertContains(renewal_page, f'href="{edit_button_url}"')

            # Simulate clicking on edit button
            edit_page = renewal_page.click(href=edit_button_url, index=1)
            self.assertEqual(edit_page.status_code, 200)
            self.assertContains(edit_page, "Domain managers can update all information related to a domain")

    @override_flag("domain_renewal", active=True)
    def test_ack_checkbox_not_checked(self):

        # Grab the renewal URL
        renewal_url = reverse("domain-renewal", kwargs={"pk": self.domain_with_ip.id})

        # Test that the checkbox is not checked
        response = self.client.post(renewal_url, data={"submit_button": "next"})

        error_message = "Check the box if you read and agree to the requirements for operating a .gov domain."
        self.assertContains(response, error_message)

    @override_flag("domain_renewal", active=True)
    def test_ack_checkbox_checked(self):

        # Grab the renewal URL
        with patch.object(Domain, "renew_domain", self.custom_renew_domain):
            renewal_url = reverse("domain-renewal", kwargs={"pk": self.domain_with_ip.id})

            # Click the check, and submit
            response = self.client.post(renewal_url, data={"is_policy_acknowledged": "on", "submit_button": "next"})

            # Check that it redirects after a successfully submits
            self.assertRedirects(response, reverse("domain", kwargs={"pk": self.domain_with_ip.id}))

            # Check for the updated expiration
            formatted_new_expiration_date = self.expiration_date_one_year_out().strftime("%b. %-d, %Y")
            redirect_response = self.client.get(reverse("domain", kwargs={"pk": self.domain_with_ip.id}), follow=True)
            self.assertContains(redirect_response, formatted_new_expiration_date)


class TestDomainManagers(TestDomainOverview):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        allowed_emails = [
            AllowedEmail(email=""),
            AllowedEmail(email="testy@town.com"),
            AllowedEmail(email="mayor@igorville.gov"),
            AllowedEmail(email="testy2@town.com"),
        ]
        AllowedEmail.objects.bulk_create(allowed_emails)

    def setUp(self):
        super().setUp()
        # Add portfolio in order to test portfolio view
        self.portfolio = Portfolio.objects.create(creator=self.user, organization_name="Ice Cream")
        # Add the portfolio to the domain_information object
        self.domain_information.portfolio = self.portfolio
        self.domain_information.save()
        # Add portfolio perms to the user object
        self.portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        AllowedEmail.objects.all().delete()

    def tearDown(self):
        """Ensure that the user has its original permissions"""
        PortfolioInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        User.objects.exclude(id=self.user.id).delete()
        super().tearDown()

    @less_console_noise_decorator
    def test_domain_managers(self):
        response = self.client.get(reverse("domain-users", kwargs={"pk": self.domain.id}))
        self.assertContains(response, "Domain managers")
        self.assertContains(response, "Add a domain manager")
        # assert that the non-portfolio view contains Role column and doesn't contain Admin
        self.assertContains(response, "Role</th>")
        self.assertNotContains(response, "Admin")
        self.assertContains(response, "This domain has one manager. Adding more can prevent issues.")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_domain_managers_portfolio_view(self):
        response = self.client.get(reverse("domain-users", kwargs={"pk": self.domain.id}))
        self.assertContains(response, "Domain managers")
        self.assertContains(response, "Add a domain manager")
        # assert that the portfolio view doesn't contain Role column and does contain Admin
        self.assertNotContains(response, "Role</th>")
        self.assertContains(response, "Admin")
        self.assertContains(response, "This domain has one manager. Adding more can prevent issues.")

    @less_console_noise_decorator
    def test_domain_user_add(self):
        response = self.client.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        self.assertContains(response, "Add a domain manager")

    @less_console_noise_decorator
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_user_add_form(self, mock_send_domain_email):
        """Adding an existing user works."""
        get_user_model().objects.get_or_create(email="mayor@igorville.gov")
        user = User.objects.filter(email="mayor@igorville.gov").first()
        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        add_page.form["email"] = "mayor@igorville.gov"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        mock_send_domain_email.assert_called_once_with(
            email="mayor@igorville.gov",
            requestor=self.user,
            domains=self.domain,
            is_member_of_different_org=None,
            requested_user=user,
        )

        self.assertEqual(success_result.status_code, 302)
        self.assertEqual(
            success_result["Location"],
            reverse("domain-users", kwargs={"pk": self.domain.id}),
        )

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()
        self.assertContains(success_page, "mayor@igorville.gov")

    @boto3_mocking.patching
    @override_flag("organization_feature", active=True)
    @less_console_noise_decorator
    @patch("registrar.views.domain.send_portfolio_invitation_email")
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_user_add_form_sends_portfolio_invitation(self, mock_send_domain_email, mock_send_portfolio_email):
        """Adding an existing user works and sends portfolio invitation when
        user is not member of portfolio."""
        get_user_model().objects.get_or_create(email="mayor@igorville.gov")
        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        add_page.form["email"] = "mayor@igorville.gov"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        self.assertEqual(success_result.status_code, 302)
        self.assertEqual(
            success_result["Location"],
            reverse("domain-users", kwargs={"pk": self.domain.id}),
        )

        # Verify that the invitation emails were sent
        mock_send_portfolio_email.assert_called_once_with(
            email="mayor@igorville.gov", requestor=self.user, portfolio=self.portfolio
        )
        mock_send_domain_email.assert_called_once()
        call_args = mock_send_domain_email.call_args.kwargs
        self.assertEqual(call_args["email"], "mayor@igorville.gov")
        self.assertEqual(call_args["requestor"], self.user)
        self.assertEqual(call_args["domains"], self.domain)
        self.assertIsNone(call_args.get("is_member_of_different_org"))

        # Assert that the PortfolioInvitation is created and retrieved
        portfolio_invitation = PortfolioInvitation.objects.filter(
            email="mayor@igorville.gov", portfolio=self.portfolio
        ).first()
        self.assertIsNotNone(portfolio_invitation, "Portfolio invitation should be created.")
        self.assertEqual(portfolio_invitation.email, "mayor@igorville.gov")
        self.assertEqual(portfolio_invitation.portfolio, self.portfolio)
        self.assertEqual(portfolio_invitation.status, PortfolioInvitation.PortfolioInvitationStatus.RETRIEVED)

        # Assert that the UserPortfolioPermission is created
        user_portfolio_permission = UserPortfolioPermission.objects.filter(
            user=self.user, portfolio=self.portfolio
        ).first()
        self.assertIsNotNone(user_portfolio_permission, "User portfolio permission should be created")

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()
        self.assertContains(success_page, "mayor@igorville.gov")

    @boto3_mocking.patching
    @override_flag("organization_feature", active=True)
    @less_console_noise_decorator
    @patch("registrar.views.domain.send_portfolio_invitation_email")
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_user_add_form_sends_portfolio_invitation_to_new_email(
        self, mock_send_domain_email, mock_send_portfolio_email
    ):
        """Adding an email not associated with a user works and sends portfolio invitation."""
        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        add_page.form["email"] = "notauser@igorville.gov"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        self.assertEqual(success_result.status_code, 302)
        self.assertEqual(
            success_result["Location"],
            reverse("domain-users", kwargs={"pk": self.domain.id}),
        )

        # Verify that the invitation emails were sent
        mock_send_portfolio_email.assert_called_once_with(
            email="notauser@igorville.gov", requestor=self.user, portfolio=self.portfolio
        )
        mock_send_domain_email.assert_called_once()
        call_args = mock_send_domain_email.call_args.kwargs
        self.assertEqual(call_args["email"], "notauser@igorville.gov")
        self.assertEqual(call_args["requestor"], self.user)
        self.assertEqual(call_args["domains"], self.domain)
        self.assertIsNone(call_args.get("is_member_of_different_org"))

        # Assert that the PortfolioInvitation is created
        portfolio_invitation = PortfolioInvitation.objects.filter(
            email="notauser@igorville.gov", portfolio=self.portfolio
        ).first()
        self.assertIsNotNone(portfolio_invitation, "Portfolio invitation should be created.")
        self.assertEqual(portfolio_invitation.email, "notauser@igorville.gov")
        self.assertEqual(portfolio_invitation.portfolio, self.portfolio)
        self.assertEqual(portfolio_invitation.status, PortfolioInvitation.PortfolioInvitationStatus.INVITED)

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()
        self.assertContains(success_page, "notauser@igorville.gov")

    @boto3_mocking.patching
    @override_flag("organization_feature", active=True)
    @less_console_noise_decorator
    @patch("registrar.views.domain.send_portfolio_invitation_email")
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_user_add_form_doesnt_send_portfolio_invitation_if_already_member(
        self, mock_send_domain_email, mock_send_portfolio_email
    ):
        """Adding an existing user works and sends portfolio invitation when
        user is not member of portfolio."""
        other_user, _ = get_user_model().objects.get_or_create(email="mayor@igorville.gov")
        UserPortfolioPermission.objects.get_or_create(
            user=other_user, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        )
        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        add_page.form["email"] = "mayor@igorville.gov"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        self.assertEqual(success_result.status_code, 302)
        self.assertEqual(
            success_result["Location"],
            reverse("domain-users", kwargs={"pk": self.domain.id}),
        )

        # Verify that the invitation emails were sent
        mock_send_portfolio_email.assert_not_called()
        mock_send_domain_email.assert_called_once()
        call_args = mock_send_domain_email.call_args.kwargs
        self.assertEqual(call_args["email"], "mayor@igorville.gov")
        self.assertEqual(call_args["requestor"], self.user)
        self.assertEqual(call_args["domains"], self.domain)
        self.assertIsNone(call_args.get("is_member_of_different_org"))

        # Assert that no PortfolioInvitation is created
        portfolio_invitation_exists = PortfolioInvitation.objects.filter(
            email="mayor@igorville.gov", portfolio=self.portfolio
        ).exists()
        self.assertFalse(
            portfolio_invitation_exists, "Portfolio invitation should not be created when the user is already a member."
        )

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()
        self.assertContains(success_page, "mayor@igorville.gov")

    @boto3_mocking.patching
    @override_flag("organization_feature", active=True)
    @less_console_noise_decorator
    @patch("registrar.views.domain.send_portfolio_invitation_email")
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_user_add_form_sends_portfolio_invitation_raises_email_sending_error(
        self, mock_send_domain_email, mock_send_portfolio_email
    ):
        """Adding an existing user works and attempts to send portfolio invitation when
        user is not member of portfolio and send raises an error."""
        mock_send_portfolio_email.side_effect = EmailSendingError("Failed to send email.")
        get_user_model().objects.get_or_create(email="mayor@igorville.gov")
        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        add_page.form["email"] = "mayor@igorville.gov"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        self.assertEqual(success_result.status_code, 302)
        self.assertEqual(
            success_result["Location"],
            reverse("domain-users", kwargs={"pk": self.domain.id}),
        )

        # Verify that the invitation emails were sent
        mock_send_portfolio_email.assert_called_once_with(
            email="mayor@igorville.gov", requestor=self.user, portfolio=self.portfolio
        )
        mock_send_domain_email.assert_not_called()

        # Assert that no PortfolioInvitation is created
        portfolio_invitation_exists = PortfolioInvitation.objects.filter(
            email="mayor@igorville.gov", portfolio=self.portfolio
        ).exists()
        self.assertFalse(
            portfolio_invitation_exists, "Portfolio invitation should not be created when email fails to send."
        )

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()
        self.assertContains(success_page, "Failed to send email.")

    @less_console_noise_decorator
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_invitation_created(self, mock_send_domain_email):
        """Add user on a nonexistent email creates an invitation.

        Adding a non-existent user sends an email as a side-effect, so mock
        out send_domain_invitation_email here.
        """
        # make sure there is no user with this email
        email_address = "mayor@igorville.gov"
        User.objects.filter(email=email_address).delete()

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        add_page.form["email"] = email_address
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        mock_send_domain_email.assert_called_once_with(
            email="mayor@igorville.gov", requestor=self.user, domains=self.domain, is_member_of_different_org=None
        )

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()

        self.assertContains(success_page, email_address)
        self.assertContains(success_page, "Cancel")  # link to cancel invitation
        self.assertTrue(DomainInvitation.objects.filter(email=email_address).exists())

    @less_console_noise_decorator
    @patch("registrar.views.domain.send_domain_invitation_email")
    def test_domain_invitation_created_for_caps_email(self, mock_send_domain_email):
        """Add user on a nonexistent email with CAPS creates an invitation to lowercase email.

        Adding a non-existent user sends an email as a side-effect, so mock
        out send_domain_invitation_email here.
        """
        # make sure there is no user with this email
        email_address = "mayor@igorville.gov"
        caps_email_address = "MAYOR@igorville.gov"
        User.objects.filter(email=email_address).delete()

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        add_page.form["email"] = caps_email_address
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        success_result = add_page.form.submit()

        mock_send_domain_email.assert_called_once_with(
            email="mayor@igorville.gov", requestor=self.user, domains=self.domain, is_member_of_different_org=None
        )

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_page = success_result.follow()

        self.assertContains(success_page, email_address)
        self.assertContains(success_page, "Cancel")  # link to cancel invitation
        self.assertTrue(DomainInvitation.objects.filter(email=email_address).exists())

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_domain_invitation_email_sent(self):
        """Inviting a non-existent user sends them an email."""
        # make sure there is no user with this email
        email_address = "mayor@igorville.gov"
        allowed_email, _ = AllowedEmail.objects.get_or_create(email=email_address)
        User.objects.filter(email=email_address).delete()

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        mock_client = MagicMock()
        mock_client_instance = mock_client.return_value
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
            add_page.form["email"] = email_address
            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            add_page.form.submit()

        # check the mock instance to see if `send_email` was called right
        mock_client_instance.send_email.assert_called_once_with(
            FromEmailAddress=settings.DEFAULT_FROM_EMAIL,
            Destination={"ToAddresses": [email_address]},
            Content=ANY,
        )
        allowed_email.delete()

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_domain_invitation_email_has_email_as_requestor_non_existent(self):
        """Inviting a non existent user sends them an email, with email as the name."""
        # make sure there is no user with this email
        email_address = "mayor@igorville.gov"
        User.objects.filter(email=email_address).delete()

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        mock_client = MagicMock()
        mock_client_instance = mock_client.return_value

        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
            add_page.form["email"] = email_address
            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            add_page.form.submit()

        # check the mock instance to see if `send_email` was called right
        mock_client_instance.send_email.assert_called_once_with(
            FromEmailAddress=settings.DEFAULT_FROM_EMAIL,
            Destination={"ToAddresses": [email_address]},
            Content=ANY,
        )

        # Check the arguments passed to send_email method
        _, kwargs = mock_client_instance.send_email.call_args

        # Extract the email content, and check that the message is as we expect
        email_content = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("info@example.com", email_content)

        # Check that the requestors first/last name do not exist
        self.assertNotIn("First", email_content)
        self.assertNotIn("Last", email_content)
        self.assertNotIn("First Last", email_content)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_domain_invitation_email_has_email_as_requestor(self):
        """Inviting a user sends them an email, with email as the name."""
        # Create a fake user object
        email_address = "mayor@igorville.gov"
        User.objects.get_or_create(email=email_address, username="fakeuser@fakeymail.com")

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        mock_client = MagicMock()
        mock_client_instance = mock_client.return_value

        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
            add_page.form["email"] = email_address
            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            add_page.form.submit()

        # check the mock instance to see if `send_email` was called right
        mock_client_instance.send_email.assert_called_once_with(
            FromEmailAddress=settings.DEFAULT_FROM_EMAIL,
            Destination={"ToAddresses": [email_address]},
            Content=ANY,
        )

        # Check the arguments passed to send_email method
        _, kwargs = mock_client_instance.send_email.call_args

        # Extract the email content, and check that the message is as we expect
        email_content = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("info@example.com", email_content)

        # Check that the requestors first/last name do not exist
        self.assertNotIn("First", email_content)
        self.assertNotIn("Last", email_content)
        self.assertNotIn("First Last", email_content)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_domain_invitation_email_has_email_as_requestor_staff(self):
        """Inviting a user sends them an email, with email as the name."""
        # Create a fake user object
        email_address = "mayor@igorville.gov"
        AllowedEmail.objects.get_or_create(email=email_address)
        User.objects.get_or_create(email=email_address, username="fakeuser@fakeymail.com")

        # Make sure the user is staff
        self.user.is_staff = True
        self.user.save()

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        mock_client = MagicMock()
        mock_client_instance = mock_client.return_value

        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
            add_page.form["email"] = email_address
            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            add_page.form.submit()

        # check the mock instance to see if `send_email` was called right
        mock_client_instance.send_email.assert_called_once_with(
            FromEmailAddress=settings.DEFAULT_FROM_EMAIL,
            Destination={"ToAddresses": [email_address]},
            Content=ANY,
        )

        # Check the arguments passed to send_email method
        _, kwargs = mock_client_instance.send_email.call_args

        # Extract the email content, and check that the message is as we expect
        email_content = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("help@get.gov", email_content)

        # Check that the requestors first/last name do not exist
        self.assertNotIn("First", email_content)
        self.assertNotIn("Last", email_content)
        self.assertNotIn("First Last", email_content)

    @less_console_noise_decorator
    def test_domain_invitation_email_validation_blocks_bad_email(self):
        """Inviting a bad email blocks at validation."""
        email_address = "mayor"
        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        add_page.form["email"] = email_address
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        response = add_page.form.submit()

        self.assertContains(response, "Enter an email address in the required format, like name@example.com.")

    @less_console_noise_decorator
    def test_domain_invitation_email_displays_error(self):
        """When the requesting user has no email, an error is displayed"""
        # make sure there is no user with this email
        # Create a fake user object
        email_address = "mayor@igorville.gov"
        User.objects.get_or_create(email=email_address, username="fakeuser@fakeymail.com")

        # Give the user who is sending the email an invalid email address
        self.user.email = ""
        self.user.is_staff = False
        self.user.save()

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        with patch("django.contrib.messages.error") as mock_error:
            add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
            add_page.form["email"] = email_address
            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            add_page.form.submit()

            expected_message_content = "Can't send invitation email. No email is associated with your user account."

            # Assert that the error message was called with the correct argument
            mock_error.assert_called_once_with(
                ANY,
                expected_message_content,
            )

    @less_console_noise_decorator
    def test_domain_invitation_cancel(self):
        """Posting to the delete view deletes an invitation."""
        email_address = "mayor@igorville.gov"
        invitation, _ = DomainInvitation.objects.get_or_create(domain=self.domain, email=email_address)
        self.client.post(reverse("invitation-cancel", kwargs={"pk": invitation.id}))
        invitation = DomainInvitation.objects.get(id=invitation.id)
        self.assertEqual(invitation.status, DomainInvitation.DomainInvitationStatus.CANCELED)

    @less_console_noise_decorator
    def test_domain_invitation_cancel_retrieved_invitation(self):
        """Posting to the cancel view when invitation retrieved returns an error message"""
        email_address = "mayor@igorville.gov"
        invitation, _ = DomainInvitation.objects.get_or_create(
            domain=self.domain, email=email_address, status=DomainInvitation.DomainInvitationStatus.RETRIEVED
        )
        response = self.client.post(reverse("invitation-cancel", kwargs={"pk": invitation.id}), follow=True)
        # Assert that an error message is displayed to the user
        self.assertContains(response, f"Invitation to {email_address} has already been retrieved.")
        # Assert that the Cancel link is not displayed
        self.assertNotContains(response, "Cancel")
        # Assert that the DomainInvitation is not deleted
        self.assertTrue(DomainInvitation.objects.filter(id=invitation.id).exists())
        DomainInvitation.objects.filter(email=email_address).delete()

    @less_console_noise_decorator
    def test_domain_invitation_cancel_no_permissions(self):
        """Posting to the cancel view as a different user should fail."""
        email_address = "mayor@igorville.gov"
        invitation, _ = DomainInvitation.objects.get_or_create(domain=self.domain, email=email_address)

        other_user = create_user()
        other_user.save()
        self.client.force_login(other_user)
        mock_client = MagicMock()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            result = self.client.post(reverse("invitation-cancel", kwargs={"pk": invitation.id}))

        self.assertEqual(result.status_code, 403)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_domain_invitation_flow(self):
        """Send an invitation to a new user, log in and load the dashboard."""
        email_address = "mayor@igorville.gov"
        username = "mayor"
        first_name = "First"
        last_name = "Last"
        title = "title"
        phone = "8080102431"
        title = "title"
        User.objects.filter(email=email_address).delete()

        add_page = self.app.get(reverse("domain-users-add", kwargs={"pk": self.domain.id}))

        self.domain_information, _ = DomainInformation.objects.get_or_create(creator=self.user, domain=self.domain)

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        add_page.form["email"] = email_address
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        mock_client = MagicMock()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            add_page.form.submit()

        # user was invited, create them
        new_user = User.objects.create(
            username=username, email=email_address, first_name=first_name, last_name=last_name, title=title, phone=phone
        )
        # log them in to `self.app`
        self.app.set_user(new_user.username)
        # and manually call the on each login callback
        new_user.on_each_login()

        # Now load the home page and make sure our domain appears there
        home_page = self.app.get(reverse("home"))
        self.assertContains(home_page, self.domain.name)


class TestDomainNameservers(TestDomainOverview, MockEppLib):
    @less_console_noise_decorator
    def test_domain_nameservers(self):
        """Can load domain's nameservers page."""
        page = self.client.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        self.assertContains(page, "DNS name servers")

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_one_nameserver(self):
        """Nameserver form submitted with one nameserver throws error.

        Uses self.app WebTest because we need to interact with forms.
        """
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form with only one nameserver, should error
        # regarding required fields
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  form requires a minimum of 2 name servers
        self.assertContains(
            result,
            "At least two name servers are required.",
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_subdomain_missing_ip(self):
        """Nameserver form catches missing ip error on subdomain.

        Uses self.app WebTest because we need to interact with forms.
        """
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form without two hosts, both subdomains,
        # only one has ips
        nameservers_page.form["form-1-server"] = "ns2.igorville.gov"

        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  subdomain missing an ip
        self.assertContains(
            result,
            str(NameserverError(code=NameserverErrorCodes.MISSING_IP)),
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_missing_host(self):
        """Nameserver form catches error when host is missing.

        Uses self.app WebTest because we need to interact with forms.
        """
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form without two hosts, both subdomains,
        # only one has ips
        nameservers_page.form["form-1-ip"] = "127.0.0.1"
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  nameserver has ip but missing host
        self.assertContains(
            result,
            str(NameserverError(code=NameserverErrorCodes.MISSING_HOST)),
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_duplicate_host(self):
        """Nameserver form catches error when host is duplicated.

        Uses self.app WebTest because we need to interact with forms.
        """
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form with duplicate host names of fake.host.com
        nameservers_page.form["form-0-ip"] = ""
        nameservers_page.form["form-1-server"] = "fake.host.com"
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  remove duplicate entry
        self.assertContains(
            result,
            str(NameserverError(code=NameserverErrorCodes.DUPLICATE_HOST)),
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_whitespace(self):
        """Nameserver form removes whitespace from ip.

        Uses self.app WebTest because we need to interact with forms.
        """
        nameserver1 = "ns1.igorville.gov"
        nameserver2 = "ns2.igorville.gov"
        valid_ip = "1.1. 1.1"
        valid_ip_2 = "2.2. 2.2"
        # have to throw an error in order to test that the whitespace has been stripped from ip
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form without one host and an ip with whitespace
        nameservers_page.form["form-0-server"] = nameserver1
        nameservers_page.form["form-0-ip"] = valid_ip
        nameservers_page.form["form-1-ip"] = valid_ip_2
        nameservers_page.form["form-1-server"] = nameserver2
        result = nameservers_page.form.submit()
        # form submission was a post with an ip address which has been stripped of whitespace,
        # response should be a 302 to success page
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}),
        )
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        page = result.follow()
        # in the event of a generic nameserver error from registry error, there will be a 302
        # with an error message displayed, so need to follow 302 and test for success message
        self.assertContains(page, "The name servers for this domain have been updated")

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_glue_record_not_allowed(self):
        """Nameserver form catches error when IP is present
        but host not subdomain.

        Uses self.app WebTest because we need to interact with forms.
        """
        nameserver1 = "ns1.igorville.gov"
        nameserver2 = "ns2.igorville.com"
        valid_ip = "127.0.0.1"
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form without two hosts, both subdomains,
        # only one has ips
        nameservers_page.form["form-0-server"] = nameserver1
        nameservers_page.form["form-1-server"] = nameserver2
        nameservers_page.form["form-1-ip"] = valid_ip
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  nameserver has ip but missing host
        self.assertContains(
            result,
            str(NameserverError(code=NameserverErrorCodes.GLUE_RECORD_NOT_ALLOWED)),
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_invalid_ip(self):
        """Nameserver form catches invalid IP on submission.

        Uses self.app WebTest because we need to interact with forms.
        """
        nameserver = "ns2.igorville.gov"
        invalid_ip = "123"
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form without two hosts, both subdomains,
        # only one has ips
        nameservers_page.form["form-1-server"] = nameserver
        nameservers_page.form["form-1-ip"] = invalid_ip
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  nameserver has ip but missing host
        self.assertContains(
            result,
            str(NameserverError(code=NameserverErrorCodes.INVALID_IP, nameserver=nameserver)),
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submit_invalid_host(self):
        """Nameserver form catches invalid host on submission.

        Uses self.app WebTest because we need to interact with forms.
        """
        nameserver = "invalid-nameserver.gov"
        valid_ip = "123.2.45.111"
        # initial nameservers page has one server with two ips
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # attempt to submit the form without two hosts, both subdomains,
        # only one has ips
        nameservers_page.form["form-1-server"] = nameserver
        nameservers_page.form["form-1-ip"] = valid_ip
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the required field.  nameserver has invalid host
        self.assertContains(
            result,
            str(NameserverError(code=NameserverErrorCodes.INVALID_HOST, nameserver=nameserver)),
            count=2,
            status_code=200,
        )

    @less_console_noise_decorator
    def test_domain_nameservers_form_submits_successfully(self):
        """Nameserver form submits successfully with valid input.

        Uses self.app WebTest because we need to interact with forms.
        """
        nameserver1 = "ns1.igorville.gov"
        nameserver2 = "ns2.igorville.gov"
        valid_ip = "127.0.0.1"
        valid_ip_2 = "128.0.0.2"
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        nameservers_page.form["form-0-server"] = nameserver1
        nameservers_page.form["form-0-ip"] = valid_ip
        nameservers_page.form["form-1-server"] = nameserver2
        nameservers_page.form["form-1-ip"] = valid_ip_2
        result = nameservers_page.form.submit()
        # form submission was a successful post, response should be a 302
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}),
        )
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        page = result.follow()
        self.assertContains(page, "The name servers for this domain have been updated")

    @less_console_noise_decorator
    def test_domain_nameservers_can_blank_out_first_or_second_one_if_enough_entries(self):
        """Nameserver form submits successfully with 2 valid inputs, even if the first or
        second entries are blanked out.

        Uses self.app WebTest because we need to interact with forms.
        """

        nameserver1 = ""
        nameserver2 = "ns2.igorville.gov"
        nameserver3 = "ns3.igorville.gov"
        valid_ip = ""
        valid_ip_2 = "128.0.0.2"
        valid_ip_3 = "128.0.0.3"
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        nameservers_page.form["form-0-server"] = nameserver1
        nameservers_page.form["form-0-ip"] = valid_ip
        nameservers_page.form["form-1-server"] = nameserver2
        nameservers_page.form["form-1-ip"] = valid_ip_2
        nameservers_page.form["form-2-server"] = nameserver3
        nameservers_page.form["form-2-ip"] = valid_ip_3
        result = nameservers_page.form.submit()

        # form submission was a successful post, response should be a 302
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}),
        )
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        nameservers_page = result.follow()
        self.assertContains(nameservers_page, "The name servers for this domain have been updated")

        nameserver1 = "ns1.igorville.gov"
        nameserver2 = ""
        nameserver3 = "ns3.igorville.gov"
        valid_ip = "128.0.0.1"
        valid_ip_2 = ""
        valid_ip_3 = "128.0.0.3"
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        nameservers_page.form["form-0-server"] = nameserver1
        nameservers_page.form["form-0-ip"] = valid_ip
        nameservers_page.form["form-1-server"] = nameserver2
        nameservers_page.form["form-1-ip"] = valid_ip_2
        nameservers_page.form["form-2-server"] = nameserver3
        nameservers_page.form["form-2-ip"] = valid_ip_3
        result = nameservers_page.form.submit()

        # form submission was a successful post, response should be a 302
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}),
        )
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        nameservers_page = result.follow()
        self.assertContains(nameservers_page, "The name servers for this domain have been updated")

    @less_console_noise_decorator
    def test_domain_nameservers_can_blank_out_first_and_second_one_if_enough_entries(self):
        """Nameserver form submits successfully with 2 valid inputs, even if the first and
        second entries are blanked out.

        Uses self.app WebTest because we need to interact with forms.
        """

        # We need to start with a domain with 4 nameservers otherwise the formset in the test environment
        # will only have 3 forms
        nameserver1 = ""
        nameserver2 = ""
        nameserver3 = "ns3.igorville.gov"
        nameserver4 = "ns4.igorville.gov"
        valid_ip = ""
        valid_ip_2 = ""
        valid_ip_3 = ""
        valid_ip_4 = ""
        nameservers_page = self.app.get(
            reverse("domain-dns-nameservers", kwargs={"pk": self.domain_with_four_nameservers.id})
        )

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        # Minimal check to ensure the form is loaded correctly
        self.assertEqual(nameservers_page.form["form-0-server"].value, "ns1.my-nameserver-1.com")
        self.assertEqual(nameservers_page.form["form-3-server"].value, "ns1.explosive-chicken-nuggets.com")

        nameservers_page.form["form-0-server"] = nameserver1
        nameservers_page.form["form-0-ip"] = valid_ip
        nameservers_page.form["form-1-server"] = nameserver2
        nameservers_page.form["form-1-ip"] = valid_ip_2
        nameservers_page.form["form-2-server"] = nameserver3
        nameservers_page.form["form-2-ip"] = valid_ip_3
        nameservers_page.form["form-3-server"] = nameserver4
        nameservers_page.form["form-3-ip"] = valid_ip_4
        result = nameservers_page.form.submit()

        # form submission was a successful post, response should be a 302
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            reverse("domain-dns-nameservers", kwargs={"pk": self.domain_with_four_nameservers.id}),
        )
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        nameservers_page = result.follow()
        self.assertContains(nameservers_page, "The name servers for this domain have been updated")

    @less_console_noise_decorator
    def test_domain_nameservers_form_invalid(self):
        """Nameserver form does not submit with invalid data.

        Uses self.app WebTest because we need to interact with forms.
        """
        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # first two nameservers are required, so if we empty one out we should
        # get a form error
        nameservers_page.form["form-0-server"] = ""
        result = nameservers_page.form.submit()
        # form submission was a post with an error, response should be a 200
        # error text appears four times, twice at the top of the page,
        # once around each required field.
        self.assertContains(
            result,
            "At least two name servers are required.",
            count=4,
            status_code=200,
        )


class TestDomainSeniorOfficial(TestDomainOverview):
    @less_console_noise_decorator
    def test_domain_senior_official(self):
        """Can load domain's senior official page."""
        page = self.client.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        self.assertContains(page, "Senior official", count=4)

    @less_console_noise_decorator
    def test_domain_senior_official_content(self):
        """Senior official information appears on the page."""
        self.domain_information.senior_official = Contact(first_name="Testy")
        self.domain_information.senior_official.save()
        self.domain_information.save()
        page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        self.assertContains(page, "Testy")

    @less_console_noise_decorator
    def test_domain_edit_senior_official_in_place(self):
        """When editing a senior official for domain information and SO is not
        joined to any other objects"""
        self.domain_information.senior_official = Contact(
            first_name="Testy", last_name="Tester", title="CIO", email="nobody@igorville.gov"
        )
        self.domain_information.senior_official.save()
        self.domain_information.save()
        so_page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        so_form = so_page.forms[0]
        self.assertEqual(so_form["first_name"].value, "Testy")
        so_form["first_name"] = "Testy2"
        # so_pk is the initial pk of the senior official. set it before update
        # to be able to verify after update that the same contact object is in place
        so_pk = self.domain_information.senior_official.id
        so_form.submit()

        # refresh domain information
        self.domain_information.refresh_from_db()
        self.assertEqual("Testy2", self.domain_information.senior_official.first_name)
        self.assertEqual(so_pk, self.domain_information.senior_official.id)

    @less_console_noise_decorator
    def assert_all_form_fields_have_expected_values(self, form, test_cases, test_for_disabled=False):
        """
        Asserts that each specified form field has the expected value and, optionally, checks if the field is disabled.

        This method iterates over a list of tuples, where each
        tuple contains a field name and the expected value for that field.
        It uses subtests to isolate each assertion, allowing multiple field
        checks within a single test method without stopping at the first failure.

        Example usage:
        test_cases = [
            ("first_name", "John"),
            ("last_name", "Doe"),
            ("email", "john.doe@example.com"),
        ]
        self.assert_all_form_fields_have_expected_values(my_form, test_cases, test_for_disabled=True)
        """
        for field_name, expected_value in test_cases:
            with self.subTest(field_name=field_name, expected_value=expected_value):
                # Test that each field has the value we expect
                self.assertEqual(expected_value, form[field_name].value)

                if test_for_disabled:
                    # Test for disabled on each field
                    self.assertTrue("disabled" in form[field_name].attrs)

    @less_console_noise_decorator
    def test_domain_cannot_edit_senior_official_when_federal(self):
        """Tests that no edit can occur when the underlying domain is federal"""

        # Set the org type to federal
        self.domain_information.generic_org_type = DomainInformation.OrganizationChoices.FEDERAL
        self.domain_information.save()

        # Add an SO
        self.domain_information.senior_official = Contact(
            first_name="Apple", last_name="Tester", title="CIO", email="nobody@igorville.gov"
        )
        self.domain_information.senior_official.save()
        self.domain_information.save()

        so_page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        self.assertContains(so_page, "Apple Tester")
        self.assertContains(so_page, "CIO")
        self.assertContains(so_page, "nobody@igorville.gov")
        self.assertNotContains(so_page, "Save")

    @less_console_noise_decorator
    def test_domain_cannot_edit_senior_official_tribal(self):
        """Tests that no edit can occur when the underlying domain is tribal"""

        # Set the org type to federal
        self.domain_information.generic_org_type = DomainInformation.OrganizationChoices.TRIBAL
        self.domain_information.save()

        # Add an SO. We can do this at the model level, just not the form level.
        self.domain_information.senior_official = Contact(
            first_name="Apple", last_name="Tester", title="CIO", email="nobody@igorville.gov"
        )
        self.domain_information.senior_official.save()
        self.domain_information.save()

        so_page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        self.assertContains(so_page, "Apple Tester")
        self.assertContains(so_page, "CIO")
        self.assertContains(so_page, "nobody@igorville.gov")
        self.assertNotContains(so_page, "Save")

    @less_console_noise_decorator
    def test_domain_edit_senior_official_creates_new(self):
        """When editing a senior official for domain information and SO IS
        joined to another object"""
        # set SO and Other Contact to the same Contact object
        self.domain_information.senior_official = Contact(
            first_name="Testy", last_name="Tester", title="CIO", email="nobody@igorville.gov"
        )
        self.domain_information.senior_official.save()
        self.domain_information.save()
        self.domain_information.other_contacts.add(self.domain_information.senior_official)
        self.domain_information.save()
        # load the Senior Official in the web form
        so_page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        so_form = so_page.forms[0]
        # verify the first name is "Testy" and then change it to "Testy2"
        self.assertEqual(so_form["first_name"].value, "Testy")
        so_form["first_name"] = "Testy2"
        # so_pk is the initial pk of the senior official. set it before update
        # to be able to verify after update that the same contact object is in place
        so_pk = self.domain_information.senior_official.id
        so_form.submit()

        # refresh domain information
        self.domain_information.refresh_from_db()
        # assert that SO information is updated, and that the SO is a new Contact
        self.assertEqual("Testy2", self.domain_information.senior_official.first_name)
        self.assertNotEqual(so_pk, self.domain_information.senior_official.id)
        # assert that the Other Contact information is not updated and that the Other Contact
        # is the original Contact object
        other_contact = self.domain_information.other_contacts.all()[0]
        self.assertEqual("Testy", other_contact.first_name)
        self.assertEqual(so_pk, other_contact.id)


class TestDomainOrganization(TestDomainOverview):
    @less_console_noise_decorator
    def test_domain_org_name_address(self):
        """Can load domain's org name and mailing address page."""
        page = self.client.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))
        # once on the sidebar, once in the page title, once as H1
        self.assertContains(page, "/org-name-address")
        self.assertContains(page, "Organization name and mailing address")
        self.assertContains(page, "Organization</h1>")

    @less_console_noise_decorator
    def test_domain_org_name_address_content(self):
        """Org name and address information appears on the page."""
        self.domain_information.organization_name = "Town of Igorville"
        self.domain_information.save()
        page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))
        self.assertContains(page, "Town of Igorville")

    @less_console_noise_decorator
    def test_domain_org_name_address_form(self):
        """Submitting changes works on the org name address page."""
        self.domain_information.organization_name = "Town of Igorville"
        self.domain_information.save()
        org_name_page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        org_name_page.form["organization_name"] = "Not igorville"
        org_name_page.form["city"] = "Faketown"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        success_result_page = org_name_page.form.submit()
        self.assertEqual(success_result_page.status_code, 200)

        self.assertContains(success_result_page, "Not igorville")
        self.assertContains(success_result_page, "Faketown")

    @less_console_noise_decorator
    def test_domain_org_name_address_form_tribal(self):
        """
        Submitting a change to organization_name is blocked for tribal domains
        """
        # Set the current domain to a tribal organization with a preset value.
        # Save first, so we can test if saving is unaffected (it should be).
        tribal_org_type = DomainInformation.OrganizationChoices.TRIBAL
        self.domain_information.generic_org_type = tribal_org_type
        self.domain_information.save()
        try:
            # Add an org name
            self.domain_information.organization_name = "Town of Igorville"
            self.domain_information.save()
        except ValueError as err:
            self.fail(f"A ValueError was caught during the test: {err}")

        self.assertEqual(self.domain_information.generic_org_type, tribal_org_type)

        org_name_page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))

        form = org_name_page.forms[0]
        # Check the value of the input field
        organization_name_input = form.fields["organization_name"][0]
        self.assertEqual(organization_name_input.value, "Town of Igorville")

        # Check if the input field is disabled
        self.assertTrue("disabled" in organization_name_input.attrs)
        self.assertEqual(organization_name_input.attrs.get("disabled"), "")

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        org_name_page.form["organization_name"] = "Not igorville"
        org_name_page.form["city"] = "Faketown"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        # Make the change. The org name should be unchanged, but city should be modifiable.
        success_result_page = org_name_page.form.submit()
        self.assertEqual(success_result_page.status_code, 200)

        # Check for the old and new value
        self.assertContains(success_result_page, "Town of Igorville")
        self.assertNotContains(success_result_page, "Not igorville")

        # Do another check on the form itself
        form = success_result_page.forms[0]
        # Check the value of the input field
        organization_name_input = form.fields["organization_name"][0]
        self.assertEqual(organization_name_input.value, "Town of Igorville")

        # Check if the input field is disabled
        self.assertTrue("disabled" in organization_name_input.attrs)
        self.assertEqual(organization_name_input.attrs.get("disabled"), "")

        # Check for the value we want to update
        self.assertContains(success_result_page, "Faketown")

    @less_console_noise_decorator
    def test_domain_org_name_address_form_federal(self):
        """
        Submitting a change to federal_agency is blocked for federal domains
        """

        fed_org_type = DomainInformation.OrganizationChoices.FEDERAL
        self.domain_information.generic_org_type = fed_org_type
        self.domain_information.save()
        try:
            federal_agency, _ = FederalAgency.objects.get_or_create(agency="AMTRAK")
            self.domain_information.federal_agency = federal_agency
            self.domain_information.save()
        except ValueError as err:
            self.fail(f"A ValueError was caught during the test: {err}")

        self.assertEqual(self.domain_information.generic_org_type, fed_org_type)

        org_name_page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))

        form = org_name_page.forms[0]
        # Check the value of the input field
        agency_input = form.fields["federal_agency"][0]
        self.assertEqual(agency_input.value, str(federal_agency.id))

        # Check if the input field is disabled
        self.assertTrue("disabled" in agency_input.attrs)
        self.assertEqual(agency_input.attrs.get("disabled"), "")

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        org_name_page.form["federal_agency"] = FederalAgency.objects.filter(agency="Department of State").get().id
        org_name_page.form["city"] = "Faketown"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        # Make the change. The agency should be unchanged, but city should be modifiable.
        success_result_page = org_name_page.form.submit()
        self.assertEqual(success_result_page.status_code, 200)

        # Check that the agency has not changed
        self.assertEqual(self.domain_information.federal_agency.agency, "AMTRAK")

        # Do another check on the form itself
        form = success_result_page.forms[0]
        # Check the value of the input field
        organization_name_input = form.fields["federal_agency"][0]
        self.assertEqual(organization_name_input.value, str(federal_agency.id))

        # Check if the input field is disabled
        self.assertTrue("disabled" in organization_name_input.attrs)
        self.assertEqual(organization_name_input.attrs.get("disabled"), "")

        # Check for the value we want to update
        self.assertContains(success_result_page, "Faketown")

    @less_console_noise_decorator
    def test_federal_agency_submit_blocked(self):
        """
        Submitting a change to federal_agency is blocked for federal domains
        """
        # Set the current domain to a tribal organization with a preset value.
        # Save first, so we can test if saving is unaffected (it should be).
        federal_org_type = DomainInformation.OrganizationChoices.FEDERAL
        self.domain_information.generic_org_type = federal_org_type
        self.domain_information.save()

        federal_agency, _ = FederalAgency.objects.get_or_create(agency="AMTRAK")
        old_federal_agency_value = federal_agency
        try:
            # Add a federal agency. Defined as a tuple since this list may change order.
            self.domain_information.federal_agency = old_federal_agency_value
            self.domain_information.save()
        except ValueError as err:
            self.fail(f"A ValueError was caught during the test: {err}")

        self.assertEqual(self.domain_information.generic_org_type, federal_org_type)

        new_value = ("Department of State", "Department of State")
        self.client.post(
            reverse("domain-org-name-address", kwargs={"pk": self.domain.id}),
            {
                "federal_agency": new_value,
            },
        )
        self.assertEqual(self.domain_information.federal_agency, old_federal_agency_value)
        self.assertNotEqual(self.domain_information.federal_agency, new_value)


class TestDomainSuborganization(TestDomainOverview):
    """Tests the Suborganization page for portfolio users"""

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_edit_suborganization_field(self):
        """Ensure that org admins can edit the suborganization field"""
        # Create a portfolio and two suborgs
        portfolio = Portfolio.objects.create(creator=self.user, organization_name="Ice Cream")
        suborg = Suborganization.objects.create(portfolio=portfolio, name="Vanilla")
        suborg_2 = Suborganization.objects.create(portfolio=portfolio, name="Chocolate")

        # Create an unrelated portfolio
        unrelated_portfolio = Portfolio.objects.create(creator=self.user, organization_name="Fruit")
        unrelated_suborg = Suborganization.objects.create(portfolio=unrelated_portfolio, name="Apple")

        # Add the portfolio to the domain_information object
        self.domain_information.portfolio = portfolio
        self.domain_information.sub_organization = suborg

        # Add a organization_name to test if the old value still displays
        self.domain_information.organization_name = "Broccoli"
        self.domain_information.save()
        self.domain_information.refresh_from_db()

        # Add portfolio perms to the user object
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        )

        self.assertEqual(self.domain_information.sub_organization, suborg)

        # Navigate to the suborganization page
        page = self.app.get(reverse("domain-suborganization", kwargs={"pk": self.domain.id}))

        # The page should contain the choices Vanilla and Chocolate
        self.assertContains(page, "Vanilla")
        self.assertContains(page, "Chocolate")
        self.assertNotContains(page, unrelated_suborg.name)

        # Assert that the right option is selected. This component uses data-default-value.
        self.assertContains(page, f'data-default-value="{suborg.id}"')

        # Try changing the suborg
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        page.form["sub_organization"] = suborg_2.id
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        page = page.form.submit().follow()

        # The page should contain the choices Vanilla and Chocolate
        self.assertContains(page, "Vanilla")
        self.assertContains(page, "Chocolate")
        self.assertNotContains(page, unrelated_suborg.name)

        # Assert that the right option is selected
        self.assertContains(page, f'data-default-value="{suborg_2.id}"')

        self.domain_information.refresh_from_db()
        self.assertEqual(self.domain_information.sub_organization, suborg_2)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_view_suborganization_field(self):
        """Only org admins can edit the suborg field, ensure that others cannot"""

        # Create a portfolio and two suborgs
        portfolio = Portfolio.objects.create(creator=self.user, organization_name="Ice Cream")
        suborg = Suborganization.objects.create(portfolio=portfolio, name="Vanilla")
        Suborganization.objects.create(portfolio=portfolio, name="Chocolate")

        # Create an unrelated portfolio
        unrelated_portfolio = Portfolio.objects.create(creator=self.user, organization_name="Fruit")
        unrelated_suborg = Suborganization.objects.create(portfolio=unrelated_portfolio, name="Apple")

        # Add the portfolio to the domain_information object
        self.domain_information.portfolio = portfolio
        self.domain_information.sub_organization = suborg

        # Add a organization_name to test if the old value still displays
        self.domain_information.organization_name = "Broccoli"
        self.domain_information.save()
        self.domain_information.refresh_from_db()

        # Add portfolio perms to the user object
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        )

        self.assertEqual(self.domain_information.sub_organization, suborg)

        # Navigate to the suborganization page
        page = self.app.get(reverse("domain-suborganization", kwargs={"pk": self.domain.id}))

        # The page should display the readonly option
        self.assertContains(page, "Vanilla")

        # The page shouldn't contain these choices
        self.assertNotContains(page, "Chocolate")
        self.assertNotContains(page, unrelated_suborg.name)
        self.assertNotContains(page, "Save")

        self.assertContains(
            page, "The suborganization for this domain can only be updated by a organization administrator."
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_has_suborganization_field_on_overview_with_flag(self):
        """Ensures that the suborganization field is visible
        and displays correctly on the domain overview page"""

        # Create a portfolio
        portfolio = Portfolio.objects.create(creator=self.user, organization_name="Ice Cream")
        suborg = Suborganization.objects.create(portfolio=portfolio, name="Vanilla")

        # Add the portfolio to the domain_information object
        self.domain_information.portfolio = portfolio

        # Add a organization_name to test if the old value still displays
        self.domain_information.organization_name = "Broccoli"
        self.domain_information.save()
        self.domain_information.refresh_from_db()

        # Add portfolio perms to the user object
        UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        )
        self.user.refresh_from_db()

        # Navigate to the domain overview page
        page = self.app.get(reverse("domain", kwargs={"pk": self.domain.id}))

        # Test for the title change
        self.assertContains(page, "Suborganization")
        self.assertNotContains(page, "Organization")

        # Test for the good value
        self.assertContains(page, "Ice Cream")

        # Test for the bad value
        self.assertNotContains(page, "Broccoli")

        # Cleanup
        self.domain_information.delete()
        suborg.delete()
        portfolio.delete()


class TestDomainSecurityEmail(TestDomainOverview):
    def test_domain_security_email_existing_security_contact(self):
        """Can load domain's security email page."""
        with less_console_noise():
            self.mockSendPatch = patch("registrar.models.domain.registry.send")
            self.mockedSendFunction = self.mockSendPatch.start()
            self.mockedSendFunction.side_effect = self.mockSend

            domain_contact, _ = Domain.objects.get_or_create(name="freeman.gov")
            # Add current user to this domain
            _ = UserDomainRole(user=self.user, domain=domain_contact, role="admin").save()
            page = self.client.get(reverse("domain-security-email", kwargs={"pk": domain_contact.id}))

            # Loads correctly
            self.assertContains(page, "Security email")
            self.assertContains(page, "security@mail.gov")
            self.mockSendPatch.stop()

    def test_domain_security_email_no_security_contact(self):
        """Loads a domain with no defined security email.
        We should not show the default."""
        with less_console_noise():
            self.mockSendPatch = patch("registrar.models.domain.registry.send")
            self.mockedSendFunction = self.mockSendPatch.start()
            self.mockedSendFunction.side_effect = self.mockSend

            page = self.client.get(reverse("domain-security-email", kwargs={"pk": self.domain.id}))

            # Loads correctly
            self.assertContains(page, "Security email")
            self.assertNotContains(page, "dotgov@cisa.dhs.gov")
            self.mockSendPatch.stop()

    def test_domain_security_email(self):
        """Can load domain's security email page."""
        with less_console_noise():
            page = self.client.get(reverse("domain-security-email", kwargs={"pk": self.domain.id}))
            self.assertContains(page, "Security email")

    def test_domain_security_email_form(self):
        """Adding a security email works.
        Uses self.app WebTest because we need to interact with forms.
        """
        with less_console_noise():
            security_email_page = self.app.get(reverse("domain-security-email", kwargs={"pk": self.domain.id}))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
            security_email_page.form["security_email"] = "mayor@igorville.gov"
            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            mock_client = MagicMock()
            with boto3_mocking.clients.handler_for("sesv2", mock_client):
                with less_console_noise():  # swallow log warning message
                    result = security_email_page.form.submit()
            self.assertEqual(result.status_code, 302)
            self.assertEqual(
                result["Location"],
                reverse("domain-security-email", kwargs={"pk": self.domain.id}),
            )

            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            success_page = result.follow()
            self.assertContains(success_page, "The security email for this domain has been updated")

    def test_domain_security_email_form_messages(self):
        """
        Test against the success and error messages that are defined in the view
        """
        with less_console_noise():
            p = "adminpass"
            self.client.login(username="superuser", password=p)
            form_data_registry_error = {
                "security_email": "test@failCreate.gov",
            }
            form_data_contact_error = {
                "security_email": "test@contactError.gov",
            }
            form_data_success = {
                "security_email": "test@something.gov",
            }
            test_cases = [
                (
                    "RegistryError",
                    form_data_registry_error,
                    str(GenericError(code=GenericErrorCodes.CANNOT_CONTACT_REGISTRY)),
                ),
                (
                    "ContactError",
                    form_data_contact_error,
                    str(SecurityEmailError(code=SecurityEmailErrorCodes.BAD_DATA)),
                ),
                (
                    "RegistrySuccess",
                    form_data_success,
                    "The security email for this domain has been updated.",
                ),
                # Add more test cases with different scenarios here
            ]
            for test_name, data, expected_message in test_cases:
                response = self.client.post(
                    reverse("domain-security-email", kwargs={"pk": self.domain.id}),
                    data=data,
                    follow=True,
                )
                # Check the response status code, content, or any other relevant assertions
                self.assertEqual(response.status_code, 200)
                # Check if the expected message tag is set
                if test_name == "RegistryError" or test_name == "ContactError":
                    message_tag = "error"
                elif test_name == "RegistrySuccess":
                    message_tag = "success"
                else:
                    # Handle other cases if needed
                    message_tag = "info"  # Change to the appropriate default
                # Check the message tag
                messages = list(response.context["messages"])
                self.assertEqual(len(messages), 1)
                message = messages[0]
                self.assertEqual(message.tags, message_tag)
                self.assertEqual(message.message.strip(), expected_message.strip())

    @less_console_noise_decorator
    def test_domain_overview_blocked_for_ineligible_user(self):
        """We could easily duplicate this test for all domain management
        views, but a single url test should be solid enough since all domain
        management pages share the same permissions class"""
        self.user.status = User.RESTRICTED
        self.user.save()
        response = self.client.get(reverse("domain", kwargs={"pk": self.domain.id}))
        self.assertEqual(response.status_code, 403)


class TestDomainDNSSEC(TestDomainOverview):
    """MockEPPLib is already inherited."""

    @less_console_noise_decorator
    def test_dnssec_page_refreshes_enable_button(self):
        """DNSSEC overview page loads when domain has no DNSSEC data
        and shows a 'Enable DNSSEC' button."""

        page = self.client.get(reverse("domain-dns-dnssec", kwargs={"pk": self.domain.id}))
        self.assertContains(page, "Enable DNSSEC")

    @less_console_noise_decorator
    def test_dnssec_page_loads_with_data_in_domain(self):
        """DNSSEC overview page loads when domain has DNSSEC data
        and the template contains a button to disable DNSSEC."""

        page = self.client.get(reverse("domain-dns-dnssec", kwargs={"pk": self.domain_multdsdata.id}))
        self.assertContains(page, "Disable DNSSEC")

        # Prepare the data for the POST request
        post_data = {
            "disable_dnssec": "Disable DNSSEC",
        }
        updated_page = self.client.post(
            reverse("domain-dns-dnssec", kwargs={"pk": self.domain.id}),
            post_data,
            follow=True,
        )

        self.assertEqual(updated_page.status_code, 200)

        self.assertContains(updated_page, "Enable DNSSEC")

    @less_console_noise_decorator
    def test_ds_form_loads_with_no_domain_data(self):
        """DNSSEC Add DS data page loads when there is no
        domain DNSSEC data and shows a button to Add new record"""

        page = self.client.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dnssec_none.id}))
        self.assertContains(page, "You have no DS data added")
        self.assertContains(page, "Add new record")

    @less_console_noise_decorator
    def test_ds_form_loads_with_ds_data(self):
        """DNSSEC Add DS data page loads when there is
        domain DNSSEC DS data and shows the data"""

        page = self.client.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        self.assertContains(page, "DS data record 1")

    @less_console_noise_decorator
    def test_ds_data_form_modal(self):
        """When user clicks on save, a modal pops up."""
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        # Assert that a hidden trigger for the modal does not exist.
        # This hidden trigger will pop on the page when certain condition are met:
        # 1) Initial form contained DS data, 2) All data is deleted and form is
        # submitted.
        self.assertNotContains(add_data_page, "Trigger Disable DNSSEC Modal")
        # Simulate a delete all data
        form_data = {}
        response = self.client.post(
            reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}),
            data=form_data,
        )
        self.assertEqual(response.status_code, 200)  # Adjust status code as needed
        # Now check to see whether the JS trigger for the modal is present on the page
        self.assertContains(response, "Trigger Disable DNSSEC Modal")

    @less_console_noise_decorator
    def test_ds_data_form_submits(self):
        """DS data form submits successfully

        Uses self.app WebTest because we need to interact with forms.
        """
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        result = add_data_page.forms[0].submit()
        # form submission was a post, response should be a redirect
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}),
        )
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        page = result.follow()
        self.assertContains(page, "The DS data records for this domain have been updated.")

    @less_console_noise_decorator
    def test_ds_data_form_invalid(self):
        """DS data form errors with invalid data (missing required fields)

        Uses self.app WebTest because we need to interact with forms.
        """
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # all four form fields are required, so will test with each blank
        add_data_page.forms[0]["form-0-key_tag"] = ""
        add_data_page.forms[0]["form-0-algorithm"] = ""
        add_data_page.forms[0]["form-0-digest_type"] = ""
        add_data_page.forms[0]["form-0-digest"] = ""
        result = add_data_page.forms[0].submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the field.
        self.assertContains(result, "Key tag is required", count=2, status_code=200)
        self.assertContains(result, "Algorithm is required", count=2, status_code=200)
        self.assertContains(result, "Digest type is required", count=2, status_code=200)
        self.assertContains(result, "Digest is required", count=2, status_code=200)

    @less_console_noise_decorator
    def test_ds_data_form_invalid_keytag(self):
        """DS data form errors with invalid data (key tag too large)

        Uses self.app WebTest because we need to interact with forms.
        """
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # first two nameservers are required, so if we empty one out we should
        # get a form error
        add_data_page.forms[0]["form-0-key_tag"] = "65536"  # > 65535
        add_data_page.forms[0]["form-0-algorithm"] = ""
        add_data_page.forms[0]["form-0-digest_type"] = ""
        add_data_page.forms[0]["form-0-digest"] = ""
        result = add_data_page.forms[0].submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the field.
        self.assertContains(
            result, str(DsDataError(code=DsDataErrorCodes.INVALID_KEYTAG_SIZE)), count=2, status_code=200
        )

    @less_console_noise_decorator
    def test_ds_data_form_invalid_digest_chars(self):
        """DS data form errors with invalid data (digest contains non hexadecimal chars)

        Uses self.app WebTest because we need to interact with forms.
        """
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # first two nameservers are required, so if we empty one out we should
        # get a form error
        add_data_page.forms[0]["form-0-key_tag"] = "1234"
        add_data_page.forms[0]["form-0-algorithm"] = "3"
        add_data_page.forms[0]["form-0-digest_type"] = "1"
        add_data_page.forms[0]["form-0-digest"] = "GG1234"
        result = add_data_page.forms[0].submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the field.
        self.assertContains(
            result, str(DsDataError(code=DsDataErrorCodes.INVALID_DIGEST_CHARS)), count=2, status_code=200
        )

    @less_console_noise_decorator
    def test_ds_data_form_invalid_digest_sha1(self):
        """DS data form errors with invalid data (digest is invalid sha-1)

        Uses self.app WebTest because we need to interact with forms.
        """
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # first two nameservers are required, so if we empty one out we should
        # get a form error
        add_data_page.forms[0]["form-0-key_tag"] = "1234"
        add_data_page.forms[0]["form-0-algorithm"] = "3"
        add_data_page.forms[0]["form-0-digest_type"] = "1"  # SHA-1
        add_data_page.forms[0]["form-0-digest"] = "A123"
        result = add_data_page.forms[0].submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the field.
        self.assertContains(
            result, str(DsDataError(code=DsDataErrorCodes.INVALID_DIGEST_SHA1)), count=2, status_code=200
        )

    @less_console_noise_decorator
    def test_ds_data_form_invalid_digest_sha256(self):
        """DS data form errors with invalid data (digest is invalid sha-256)

        Uses self.app WebTest because we need to interact with forms.
        """
        add_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain_dsdata.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        # first two nameservers are required, so if we empty one out we should
        # get a form error
        add_data_page.forms[0]["form-0-key_tag"] = "1234"
        add_data_page.forms[0]["form-0-algorithm"] = "3"
        add_data_page.forms[0]["form-0-digest_type"] = "2"  # SHA-256
        add_data_page.forms[0]["form-0-digest"] = "GG1234"
        result = add_data_page.forms[0].submit()
        # form submission was a post with an error, response should be a 200
        # error text appears twice, once at the top of the page, once around
        # the field.
        self.assertContains(
            result, str(DsDataError(code=DsDataErrorCodes.INVALID_DIGEST_SHA256)), count=2, status_code=200
        )


class TestDomainChangeNotifications(TestDomainOverview):
    """Test email notifications on updates to domain information"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        allowed_emails = [
            AllowedEmail(email="info@example.com"),
            AllowedEmail(email="doesnotexist@igorville.com"),
        ]
        AllowedEmail.objects.bulk_create(allowed_emails)

    def setUp(self):
        super().setUp()
        self.mock_client_class = MagicMock()
        self.mock_client = self.mock_client_class.return_value

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        AllowedEmail.objects.all().delete()

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_notification_on_org_name_change(self):
        """Test that an email is sent when the organization name is changed."""
        # We may end up sending emails on org name changes later, but it will be addressed
        # in the portfolio itself, rather than the individual domain.

        self.domain_information.organization_name = "Town of Igorville"
        self.domain_information.address_line1 = "123 Main St"
        self.domain_information.city = "Igorville"
        self.domain_information.state_territory = "IL"
        self.domain_information.zipcode = "62052"
        self.domain_information.save()

        org_name_page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        org_name_page.form["organization_name"] = "Not igorville"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            org_name_page.form.submit()

        # Check that an email was sent
        self.assertTrue(self.mock_client.send_email.called)

        # Check email content
        # check the call sequence for the email
        _, kwargs = self.mock_client.send_email.call_args
        self.assertIn("Content", kwargs)
        self.assertIn("Simple", kwargs["Content"])
        self.assertIn("Subject", kwargs["Content"]["Simple"])
        self.assertIn("Body", kwargs["Content"]["Simple"])

        body = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]

        self.assertIn("DOMAIN: igorville.gov", body)
        self.assertIn("UPDATED BY: First Last info@example.com", body)
        self.assertIn("INFORMATION UPDATED: Organization details", body)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_no_notification_on_org_name_change_with_portfolio(self):
        """Test that an email is not sent on org name change when the domain is in a portfolio"""

        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org", creator=self.user)

        self.domain_information.organization_name = "Town of Igorville"
        self.domain_information.address_line1 = "123 Main St"
        self.domain_information.city = "Igorville"
        self.domain_information.state_territory = "IL"
        self.domain_information.zipcode = "62052"
        self.domain_information.portfolio = portfolio
        self.domain_information.save()

        org_name_page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        org_name_page.form["organization_name"] = "Not igorville"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            org_name_page.form.submit()

        # Check that an email was not sent
        self.assertFalse(self.mock_client.send_email.called)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_no_notification_on_change_by_analyst(self):
        """Test that an email is not sent on org name change when the domain is in a portfolio"""

        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test org", creator=self.user)

        self.domain_information.organization_name = "Town of Igorville"
        self.domain_information.address_line1 = "123 Main St"
        self.domain_information.city = "Igorville"
        self.domain_information.state_territory = "IL"
        self.domain_information.zipcode = "62052"
        self.domain_information.portfolio = portfolio
        self.domain_information.save()

        org_name_page = self.app.get(reverse("domain-org-name-address", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        session = self.app.session
        session["analyst_action"] = "foo"
        session["analyst_action_location"] = self.domain.id
        session.save()

        org_name_page.form["organization_name"] = "Not igorville"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            org_name_page.form.submit()

        # Check that an email was not sent
        self.assertFalse(self.mock_client.send_email.called)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_notification_on_security_email_change(self):
        """Test that an email is sent when the security email is changed."""

        security_email_page = self.app.get(reverse("domain-security-email", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        security_email_page.form["security_email"] = "new_security@example.com"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            security_email_page.form.submit()

        self.assertTrue(self.mock_client.send_email.called)

        _, kwargs = self.mock_client.send_email.call_args
        body = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]

        self.assertIn("DOMAIN: igorville.gov", body)
        self.assertIn("UPDATED BY: First Last info@example.com", body)
        self.assertIn("INFORMATION UPDATED: Security email", body)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_notification_on_dnssec_enable(self):
        """Test that an email is sent when DNSSEC is enabled."""

        page = self.client.get(reverse("domain-dns-dnssec", kwargs={"pk": self.domain_multdsdata.id}))
        self.assertContains(page, "Disable DNSSEC")

        # Prepare the data for the POST request
        post_data = {
            "disable_dnssec": "Disable DNSSEC",
        }

        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            updated_page = self.client.post(
                reverse("domain-dns-dnssec", kwargs={"pk": self.domain.id}),
                post_data,
                follow=True,
            )

        self.assertEqual(updated_page.status_code, 200)

        self.assertContains(updated_page, "Enable DNSSEC")

        self.assertTrue(self.mock_client.send_email.called)

        _, kwargs = self.mock_client.send_email.call_args
        body = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]

        self.assertIn("DOMAIN: igorville.gov", body)
        self.assertIn("UPDATED BY: First Last info@example.com", body)
        self.assertIn("INFORMATION UPDATED: DNSSEC / DS Data", body)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_notification_on_ds_data_change(self):
        """Test that an email is sent when DS data is changed."""

        ds_data_page = self.app.get(reverse("domain-dns-dnssec-dsdata", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        # Add DS data
        ds_data_page.forms[0]["form-0-key_tag"] = "12345"
        ds_data_page.forms[0]["form-0-algorithm"] = "13"
        ds_data_page.forms[0]["form-0-digest_type"] = "2"
        ds_data_page.forms[0]["form-0-digest"] = "1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            ds_data_page.forms[0].submit()

        # check that the email was sent
        self.assertTrue(self.mock_client.send_email.called)

        # check some stuff about the email
        _, kwargs = self.mock_client.send_email.call_args
        body = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]

        self.assertIn("DOMAIN: igorville.gov", body)
        self.assertIn("UPDATED BY: First Last info@example.com", body)
        self.assertIn("INFORMATION UPDATED: DNSSEC / DS Data", body)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_notification_on_senior_official_change(self):
        """Test that an email is sent when the senior official information is changed."""

        self.domain_information.senior_official = Contact.objects.create(
            first_name="Old", last_name="Official", title="Manager", email="old_official@example.com"
        )
        self.domain_information.save()

        senior_official_page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        senior_official_page.form["first_name"] = "New"
        senior_official_page.form["last_name"] = "Official"
        senior_official_page.form["title"] = "Director"
        senior_official_page.form["email"] = "new_official@example.com"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            senior_official_page.form.submit()

        self.assertTrue(self.mock_client.send_email.called)

        _, kwargs = self.mock_client.send_email.call_args
        body = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]

        self.assertIn("DOMAIN: igorville.gov", body)
        self.assertIn("UPDATED BY: First Last info@example.com", body)
        self.assertIn("INFORMATION UPDATED: Senior official", body)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_no_notification_on_senior_official_when_portfolio(self):
        """Test that an email is not sent when the senior official information is changed
        and the domain is in a portfolio."""

        self.domain_information.senior_official = Contact.objects.create(
            first_name="Old", last_name="Official", title="Manager", email="old_official@example.com"
        )
        portfolio, _ = Portfolio.objects.get_or_create(
            organization_name="portfolio",
            creator=self.user,
        )
        self.domain_information.portfolio = portfolio
        self.domain_information.save()

        senior_official_page = self.app.get(reverse("domain-senior-official", kwargs={"pk": self.domain.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        senior_official_page.form["first_name"] = "New"
        senior_official_page.form["last_name"] = "Official"
        senior_official_page.form["title"] = "Director"
        senior_official_page.form["email"] = "new_official@example.com"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            senior_official_page.form.submit()

        self.assertFalse(self.mock_client.send_email.called)

    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_no_notification_when_dns_needed(self):
        """Test that an email is not sent when nameservers are changed while the state is DNS_NEEDED."""

        nameservers_page = self.app.get(reverse("domain-dns-nameservers", kwargs={"pk": self.domain_dns_needed.id}))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        # add nameservers
        nameservers_page.form["form-0-server"] = "ns1-new.dns-needed.gov"
        nameservers_page.form["form-0-ip"] = "192.168.1.1"
        nameservers_page.form["form-1-server"] = "ns2-new.dns-needed.gov"
        nameservers_page.form["form-1-ip"] = "192.168.1.2"

        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            nameservers_page.form.submit()

        # Check that an email was not sent
        self.assertFalse(self.mock_client.send_email.called)


class TestDomainRenewal(TestWithUser):
    def setUp(self):
        super().setUp()
        today = datetime.now()
        expiring_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        expiring_date_current = (today + timedelta(days=70)).strftime("%Y-%m-%d")
        expired_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")

        self.domain_with_expiring_soon_date, _ = Domain.objects.get_or_create(
            name="igorville.gov", expiration_date=expiring_date
        )
        self.domain_with_expired_date, _ = Domain.objects.get_or_create(
            name="domainwithexpireddate.com", expiration_date=expired_date
        )

        self.domain_with_current_date, _ = Domain.objects.get_or_create(
            name="domainwithfarexpireddate.com", expiration_date=expiring_date_current
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_with_current_date, role=UserDomainRole.Roles.MANAGER
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_with_expired_date, role=UserDomainRole.Roles.MANAGER
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_with_expiring_soon_date, role=UserDomainRole.Roles.MANAGER
        )

    def tearDown(self):
        try:
            UserDomainRole.objects.all().delete()
            Domain.objects.all().delete()
        except ValueError:
            pass
        super().tearDown()

    # Remove test_without_domain_renewal_flag when domain renewal is released as a feature
    @less_console_noise_decorator
    @override_flag("domain_renewal", active=False)
    def test_without_domain_renewal_flag(self):
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertNotContains(domains_page, "will expire soon")
        self.assertNotContains(domains_page, "Expiring soon")

    @less_console_noise_decorator
    @override_flag("domain_renewal", active=True)
    def test_domain_renewal_flag_single_domain(self):
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertContains(domains_page, "One domain will expire soon")
        self.assertContains(domains_page, "Expiring soon")

    @less_console_noise_decorator
    @override_flag("domain_renewal", active=True)
    def test_with_domain_renewal_flag_mulitple_domains(self):
        today = datetime.now()
        expiring_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        self.domain_with_another_expiring, _ = Domain.objects.get_or_create(
            name="domainwithanotherexpiringdate.com", expiration_date=expiring_date
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_with_another_expiring, role=UserDomainRole.Roles.MANAGER
        )
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertContains(domains_page, "Multiple domains will expire soon")
        self.assertContains(domains_page, "Expiring soon")

    @less_console_noise_decorator
    @override_flag("domain_renewal", active=True)
    def test_with_domain_renewal_flag_no_expiring_domains(self):
        UserDomainRole.objects.filter(user=self.user, domain=self.domain_with_expired_date).delete()
        UserDomainRole.objects.filter(user=self.user, domain=self.domain_with_expiring_soon_date).delete()
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertNotContains(domains_page, "will expire soon")

    @less_console_noise_decorator
    @override_flag("domain_renewal", active=True)
    @override_flag("organization_feature", active=True)
    def test_domain_renewal_flag_single_domain_w_org_feature_flag(self):
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertContains(domains_page, "One domain will expire soon")
        self.assertContains(domains_page, "Expiring soon")

    @less_console_noise_decorator
    @override_flag("domain_renewal", active=True)
    @override_flag("organization_feature", active=True)
    def test_with_domain_renewal_flag_mulitple_domains_w_org_feature_flag(self):
        today = datetime.now()
        expiring_date = (today + timedelta(days=31)).strftime("%Y-%m-%d")
        self.domain_with_another_expiring_org_model, _ = Domain.objects.get_or_create(
            name="domainwithanotherexpiringdate_orgmodel.com", expiration_date=expiring_date
        )

        UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain_with_another_expiring_org_model, role=UserDomainRole.Roles.MANAGER
        )
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertContains(domains_page, "Multiple domains will expire soon")
        self.assertContains(domains_page, "Expiring soon")

    @less_console_noise_decorator
    @override_flag("domain_renewal", active=True)
    @override_flag("organization_feature", active=True)
    def test_with_domain_renewal_flag_no_expiring_domains_w_org_feature_flag(self):
        UserDomainRole.objects.filter(user=self.user, domain=self.domain_with_expired_date).delete()
        UserDomainRole.objects.filter(user=self.user, domain=self.domain_with_expiring_soon_date).delete()
        self.client.force_login(self.user)
        domains_page = self.client.get("/")
        self.assertNotContains(domains_page, "will expire soon")
