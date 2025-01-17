from datetime import date
from django.test import TestCase, RequestFactory, Client, override_settings
from django.contrib.admin.sites import AdminSite
from api.tests.common import less_console_noise_decorator
from django_webtest import WebTest  # type: ignore
from django.contrib import messages
from django.urls import reverse
from registrar.admin import (
    DomainAdmin,
)
from registrar.models import (
    Domain,
    DomainRequest,
    DomainInformation,
    User,
    Host,
    Portfolio,
)
from registrar.models.public_contact import PublicContact
from registrar.models.user_domain_role import UserDomainRole
from .common import (
    MockSESClient,
    completed_domain_request,
    less_console_noise,
    create_superuser,
    create_user,
    create_ready_domain,
    MockEppLib,
    GenericTestHelper,
)
from unittest.mock import ANY, call, patch

import boto3_mocking  # type: ignore
import logging

logger = logging.getLogger(__name__)


class TestDomainAdminAsStaff(MockEppLib):
    """Test DomainAdmin class as staff user.

    Notes:
      all tests share staffuser; do not change staffuser model in tests
      tests have available staffuser, client, and admin
    """

    @classmethod
    def setUpClass(self):
        super().setUpClass()
        self.staffuser = create_user()
        self.site = AdminSite()
        self.admin = DomainAdmin(model=Domain, admin_site=self.site)
        self.factory = RequestFactory()

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost:8080")
        self.client.force_login(self.staffuser)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        Host.objects.all().delete()
        PublicContact.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainRequest.objects.all().delete()

    @classmethod
    def tearDownClass(self):
        User.objects.all().delete()
        super().tearDownClass()

    @less_console_noise_decorator
    def test_staff_can_see_cisa_region_federal(self):
        """Tests if staff can see CISA Region: N/A"""

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)
        _domain_request.approve()

        domain = _domain_request.approved_domain
        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)

        # Test if the page has the right CISA region
        expected_html = '<div class="flex-container margin-top-2"><span>CISA region: N/A</span></div>'
        # Remove whitespace from expected_html
        expected_html = "".join(expected_html.split())

        # Remove whitespace from response content
        response_content = "".join(response.content.decode().split())

        # Check if response contains expected_html
        self.assertIn(expected_html, response_content)

    @less_console_noise_decorator
    def test_staff_can_see_cisa_region_non_federal(self):
        """Tests if staff can see the correct CISA region"""

        # Create a fake domain request. State will be NY (2).
        _domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW, generic_org_type="interstate"
        )

        _domain_request.approve()

        domain = _domain_request.approved_domain

        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)

        # Test if the page has the right CISA region
        expected_html = '<div class="flex-container margin-top-2"><span>CISA region: 2</span></div>'
        # Remove whitespace from expected_html
        expected_html = "".join(expected_html.split())

        # Remove whitespace from response content
        response_content = "".join(response.content.decode().split())

        # Check if response contains expected_html
        self.assertIn(expected_html, response_content)

    @less_console_noise_decorator
    def test_analyst_can_see_inline_domain_information_in_domain_change_form(self):
        """Tests if an analyst can still see the inline domain information form"""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
        )

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)

        # Creates a Domain and DomainInformation object
        _domain_request.approve()

        domain_information = DomainInformation.objects.filter(domain_request=_domain_request).get()
        domain_information.organization_name = "MonkeySeeMonkeyDo"
        domain_information.save()

        # We use filter here rather than just domain_information.domain just to get the latest data.
        domain = Domain.objects.filter(domain_info=domain_information).get()

        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)

        # Test for data. We only need to test one since its all interconnected.
        expected_organization_name = "MonkeySeeMonkeyDo"
        self.assertContains(response, expected_organization_name)

    @less_console_noise_decorator
    def test_deletion_is_successful(self):
        """
        Scenario: Domain deletion is successful
            When the domain is deleted
            Then a user-friendly success message is returned for displaying on the web
            And `state` is set to `DELETED`
        """
        domain = create_ready_domain()
        # Put in client hold
        domain.place_client_hold()
        # Ensure everything is displaying correctly
        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Remove from registry")

        # The contents of the modal should exist before and after the post.
        # Check for the header
        self.assertContains(response, "Are you sure you want to remove this domain from the registry?")

        # Check for some of its body
        self.assertContains(response, "When a domain is removed from the registry:")

        # Check for some of the button content
        self.assertContains(response, "Yes, remove from registry")

        # Test the info dialog
        request = self.factory.post(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            {"_delete_domain": "Remove from registry", "name": domain.name},
            follow=True,
        )
        request.user = self.client
        with patch("django.contrib.messages.add_message") as mock_add_message:
            self.admin.do_delete_domain(request, domain)
            mock_add_message.assert_called_once_with(
                request,
                messages.INFO,
                "Domain city.gov has been deleted. Thanks!",
                extra_tags="",
                fail_silently=False,
            )

        # The modal should still exist
        self.assertContains(response, "Are you sure you want to remove this domain from the registry?")
        self.assertContains(response, "When a domain is removed from the registry:")
        self.assertContains(response, "Yes, remove from registry")

        self.assertEqual(domain.state, Domain.State.DELETED)

    # @less_console_noise_decorator
    def test_deletion_is_unsuccessful(self):
        """
        Scenario: Domain deletion is unsuccessful
            When the domain is deleted and has shared subdomains
            Then a user-friendly success message is returned for displaying on the web
            And `state` is not set to `DELETED`
        """
        domain, _ = Domain.objects.get_or_create(name="sharingiscaring.gov", state=Domain.State.ON_HOLD)
        # Put in client hold
        domain.place_client_hold()
        # Ensure everything is displaying correctly
        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Remove from registry")

        # The contents of the modal should exist before and after the post.
        # Check for the header
        self.assertContains(response, "Are you sure you want to remove this domain from the registry?")

        # Check for some of its body
        self.assertContains(response, "When a domain is removed from the registry:")

        # Check for some of the button content
        self.assertContains(response, "Yes, remove from registry")

        # Test the info dialog
        request = self.factory.post(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            {"_delete_domain": "Remove from registry", "name": domain.name},
            follow=True,
        )
        request.user = self.client
        with patch("django.contrib.messages.add_message") as mock_add_message:
            self.admin.do_delete_domain(request, domain)
            mock_add_message.assert_called_once_with(
                request,
                messages.ERROR,
                "Error deleting this Domain: This subdomain is being used as a hostname on another domain: ns1.sharedhost.com",  # noqa
                extra_tags="",
                fail_silently=False,
            )

        self.assertEqual(domain.state, Domain.State.ON_HOLD)

    @less_console_noise_decorator
    def test_deletion_ready_fsm_failure(self):
        """
        Scenario: Domain deletion is unsuccessful
            When an error is returned from epplibwrapper
            Then a user-friendly error message is returned for displaying on the web
            And `state` is not set to `DELETED`
        """

        domain = create_ready_domain()
        # Ensure everything is displaying correctly
        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Remove from registry")
        # Test the error
        request = self.factory.post(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            {"_delete_domain": "Remove from registry", "name": domain.name},
            follow=True,
        )
        request.user = self.client
        with patch("django.contrib.messages.add_message") as mock_add_message:
            self.admin.do_delete_domain(request, domain)
            mock_add_message.assert_called_once_with(
                request,
                messages.ERROR,
                "Error deleting this Domain: "
                "Can't switch from state 'ready' to 'deleted'"
                ", must be either 'dns_needed' or 'on_hold'",
                extra_tags="",
                fail_silently=False,
            )

        self.assertEqual(domain.state, Domain.State.READY)

    @less_console_noise_decorator
    def test_analyst_deletes_domain_idempotent(self):
        """
        Scenario: Analyst tries to delete an already deleted domain
            Given `state` is already `DELETED`
            When `domain.deletedInEpp()` is called
            Then `commands.DeleteDomain` is sent to the registry
            And Domain returns normally without an error dialog
        """
        domain = create_ready_domain()
        # Put in client hold
        domain.place_client_hold()
        # Ensure everything is displaying correctly
        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Remove from registry")
        # Test the info dialog
        request = self.factory.post(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            {"_delete_domain": "Remove from registry", "name": domain.name},
            follow=True,
        )
        request.user = self.client
        # Delete it once
        with patch("django.contrib.messages.add_message") as mock_add_message:
            self.admin.do_delete_domain(request, domain)
            mock_add_message.assert_called_once_with(
                request,
                messages.INFO,
                "Domain city.gov has been deleted. Thanks!",
                extra_tags="",
                fail_silently=False,
            )

        self.assertEqual(domain.state, Domain.State.DELETED)
        # Try to delete it again
        # Test the info dialog
        request = self.factory.post(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            {"_delete_domain": "Remove from registry", "name": domain.name},
            follow=True,
        )
        request.user = self.client
        with patch("django.contrib.messages.add_message") as mock_add_message:
            self.admin.do_delete_domain(request, domain)
            mock_add_message.assert_called_once_with(
                request,
                messages.INFO,
                "This domain is already deleted",
                extra_tags="",
                fail_silently=False,
            )
        self.assertEqual(domain.state, Domain.State.DELETED)


class TestDomainInformationInline(MockEppLib):
    """Test DomainAdmin class, specifically the DomainInformationInline class, as staff user.

    Notes:
      all tests share staffuser; do not change staffuser model in tests
      tests have available staffuser, client, and admin
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.staffuser = create_user()
        cls.site = AdminSite()
        cls.admin = DomainAdmin(model=Domain, admin_site=cls.site)
        cls.factory = RequestFactory()

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost:8080")
        self.client.force_login(self.staffuser)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        Host.objects.all().delete()
        UserDomainRole.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainRequest.objects.all().delete()

    @classmethod
    def tearDownClass(cls):
        User.objects.all().delete()
        super().tearDownClass()

    @less_console_noise_decorator
    def test_domain_managers_display(self):
        """Tests the custom domain managers field"""
        admin_user_1 = User.objects.create(
            username="testuser1",
            first_name="Gerald",
            last_name="Meoward",
            email="meoward@gov.gov",
        )

        domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=self.staffuser, name="fake.gov"
        )
        domain_request.approve()
        _domain_info = DomainInformation.objects.filter(domain=domain_request.approved_domain).get()
        domain = Domain.objects.filter(domain_info=_domain_info).get()

        UserDomainRole.objects.get_or_create(user=admin_user_1, domain=domain, role=UserDomainRole.Roles.MANAGER)

        admin_user_2 = User.objects.create(
            username="testuser2",
            first_name="Arnold",
            last_name="Poopy",
            email="poopy@gov.gov",
        )

        UserDomainRole.objects.get_or_create(user=admin_user_2, domain=domain, role=UserDomainRole.Roles.MANAGER)

        # Get the first inline (DomainInformationInline)
        inline_instance = self.admin.inlines[0](self.admin.model, self.admin.admin_site)

        # Call the domain_managers method
        domain_managers = inline_instance.domain_managers(domain.domain_info)

        self.assertIn(
            f'<a href="/admin/registrar/user/{admin_user_1.pk}/change/">testuser1</a>',
            domain_managers,
        )
        self.assertIn("Gerald Meoward", domain_managers)
        self.assertIn("meoward@gov.gov", domain_managers)
        self.assertIn(f'<a href="/admin/registrar/user/{admin_user_2.pk}/change/">testuser2</a>', domain_managers)
        self.assertIn("Arnold Poopy", domain_managers)
        self.assertIn("poopy@gov.gov", domain_managers)

    @less_console_noise_decorator
    def test_invited_domain_managers_display(self):
        """Tests the custom invited domain managers field"""
        admin_user_1 = User.objects.create(
            username="testuser1",
            first_name="Gerald",
            last_name="Meoward",
            email="meoward@gov.gov",
        )

        domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=self.staffuser, name="fake.gov"
        )
        domain_request.approve()
        _domain_info = DomainInformation.objects.filter(domain=domain_request.approved_domain).get()
        domain = Domain.objects.filter(domain_info=_domain_info).get()

        # domain, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)
        UserDomainRole.objects.get_or_create(user=admin_user_1, domain=domain, role=UserDomainRole.Roles.MANAGER)

        admin_user_2 = User.objects.create(
            username="testuser2",
            first_name="Arnold",
            last_name="Poopy",
            email="poopy@gov.gov",
        )

        UserDomainRole.objects.get_or_create(user=admin_user_2, domain=domain, role=UserDomainRole.Roles.MANAGER)

        # Get the first inline (DomainInformationInline)
        inline_instance = self.admin.inlines[0](self.admin.model, self.admin.admin_site)

        # Call the domain_managers method
        domain_managers = inline_instance.domain_managers(domain.domain_info)
        # domain_managers = self.admin.get_inlinesdomain_managers(self.domain)

        self.assertIn(
            f'<a href="/admin/registrar/user/{admin_user_1.pk}/change/">testuser1</a>',
            domain_managers,
        )
        self.assertIn("Gerald Meoward", domain_managers)
        self.assertIn("meoward@gov.gov", domain_managers)
        self.assertIn(f'<a href="/admin/registrar/user/{admin_user_2.pk}/change/">testuser2</a>', domain_managers)
        self.assertIn("Arnold Poopy", domain_managers)
        self.assertIn("poopy@gov.gov", domain_managers)


class TestDomainAdminWithClient(TestCase):
    """Test DomainAdmin class as super user.

    Notes:
      all tests share superuser; tests must not update superuser
      tests have available superuser, client, and admin
    """

    @classmethod
    def setUpClass(self):
        super().setUpClass()
        self.site = AdminSite()
        self.admin = DomainAdmin(model=Domain, admin_site=self.site)
        self.factory = RequestFactory()
        self.superuser = create_superuser()

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost:8080")
        self.client.force_login(self.superuser)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        Host.objects.all().delete()
        UserDomainRole.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainRequest.objects.all().delete()
        Portfolio.objects.all().delete()

    @classmethod
    def tearDownClass(self):
        User.objects.all().delete()
        super().tearDownClass()

    @less_console_noise_decorator
    def test_has_model_description(self):
        """Tests if this model has a model description on the table view"""
        response = self.client.get(
            "/admin/registrar/domain/",
            follow=True,
        )

        # Make sure that the page is loaded correctly
        self.assertEqual(response.status_code, 200)

        # Test for a description snippet
        self.assertContains(response, "This table contains all approved domains in the .gov registrar.")
        self.assertContains(response, "Show more")

    @less_console_noise_decorator
    def test_contact_fields_on_domain_change_form_have_detail_table(self):
        """Tests if the contact fields in the inlined Domain information have the detail table
        which displays title, email, and phone"""

        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
            email="meoward.jones@igorville.gov",
            phone="(555) 123 12345",
            title="Treat inspector",
        )

        # Create a fake domain request
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)
        domain_request.approve()
        _domain_info = DomainInformation.objects.filter(domain=domain_request.approved_domain).get()
        domain = Domain.objects.filter(domain_info=_domain_info).get()

        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)

        # Check that the fields have the right values.

        # == Check for the senior_official == #
        self.assertContains(response, "testy@town.com")
        self.assertContains(response, "Chief Tester")
        self.assertContains(response, "(555) 555 5555")

        # Includes things like readonly fields
        self.assertContains(response, "Testy Tester")

        # Test for the copy link
        self.assertContains(response, "copy-to-clipboard")

        # cleanup from this test
        domain.delete()
        _domain_info.delete()
        domain_request.delete()
        _creator.delete()

    @less_console_noise_decorator
    def test_domains_by_portfolio(self):
        """
        Tests that domains display for a portfolio.  And that domains outside the portfolio do not display.
        """

        portfolio, _ = Portfolio.objects.get_or_create(organization_name="Test Portfolio", creator=self.superuser)
        # Create a fake domain request and domain
        _domain_request = completed_domain_request(
            status=DomainRequest.DomainRequestStatus.IN_REVIEW, portfolio=portfolio
        )
        _domain_request.approve()

        domain = _domain_request.approved_domain
        domain2, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)
        UserDomainRole.objects.get_or_create()
        UserDomainRole.objects.get_or_create(user=self.superuser, domain=domain2, role=UserDomainRole.Roles.MANAGER)

        self.client.force_login(self.superuser)
        response = self.client.get(
            "/admin/registrar/domain/?portfolio={}".format(portfolio.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertNotContains(response, domain2.name)
        self.assertContains(response, portfolio.organization_name)

    @less_console_noise_decorator
    def test_helper_text(self):
        """
        Tests for the correct helper text on this page
        """

        # Create a ready domain with a preset expiration date
        domain, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)

        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)

        # Contains some test tools
        test_helper = GenericTestHelper(
            factory=self.factory,
            user=self.superuser,
            admin=self.admin,
            url=reverse("admin:registrar_domain_changelist"),
            model=Domain,
            client=self.client,
        )
        # These should exist in the response
        expected_values = [
            ("expiration_date", "Date the domain expires in the registry"),
            ("first_ready_at", 'Date when this domain first moved into "ready" state; date will never change'),
            ("deleted_at", 'Will appear blank unless the domain is in "deleted" state'),
        ]
        test_helper.assert_response_contains_distinct_values(response, expected_values)

    @less_console_noise_decorator
    def test_helper_text_state(self):
        """
        Tests for the correct state helper text on this page
        """

        # Add domain data
        ready_domain, _ = Domain.objects.get_or_create(name="fakeready.gov", state=Domain.State.READY)
        unknown_domain, _ = Domain.objects.get_or_create(name="fakeunknown.gov", state=Domain.State.UNKNOWN)
        dns_domain, _ = Domain.objects.get_or_create(name="fakedns.gov", state=Domain.State.DNS_NEEDED)
        hold_domain, _ = Domain.objects.get_or_create(name="fakehold.gov", state=Domain.State.ON_HOLD)
        deleted_domain, _ = Domain.objects.get_or_create(name="fakedeleted.gov", state=Domain.State.DELETED)

        # We don't need to check for all text content, just a portion of it
        expected_unknown_domain_message = "The creator of the associated domain request has not logged in to"
        expected_dns_message = "Before this domain can be used, name server addresses need"
        expected_hold_message = "While on hold, this domain"
        expected_deleted_message = "This domain was permanently removed from the registry."
        expected_messages = [
            (ready_domain, "This domain has name servers and is ready for use."),
            (unknown_domain, expected_unknown_domain_message),
            (dns_domain, expected_dns_message),
            (hold_domain, expected_hold_message),
            (deleted_domain, expected_deleted_message),
        ]

        for domain, message in expected_messages:
            with self.subTest(domain_state=domain.state):
                response = self.client.get(
                    "/admin/registrar/domain/{}/change/".format(domain.id),
                )

                # Make sure the page loaded, and that we're on the right page
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, domain.name)

                # Check that the right help text exists
                self.assertContains(response, message)

    @less_console_noise_decorator
    def test_admin_can_see_inline_domain_information_in_domain_change_form(self):
        """Tests if an admin can still see the inline domain information form"""
        # Create fake creator
        _creator = User.objects.create(
            username="MrMeoward",
            first_name="Meoward",
            last_name="Jones",
        )

        # Create a fake domain request
        _domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW, user=_creator)

        # Creates a Domain and DomainInformation object
        _domain_request.approve()

        domain_information = DomainInformation.objects.filter(domain_request=_domain_request).get()
        domain_information.organization_name = "MonkeySeeMonkeyDo"
        domain_information.save()

        # We use filter here rather than just domain_information.domain just to get the latest data.
        domain = Domain.objects.filter(domain_info=domain_information).get()

        response = self.client.get(
            "/admin/registrar/domain/{}/change/".format(domain.pk),
            follow=True,
        )

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)

        # Test for data. We only need to test one since its all interconnected.
        expected_organization_name = "MonkeySeeMonkeyDo"
        self.assertContains(response, expected_organization_name)

        # cleanup from this test
        domain.delete()
        domain_information.delete()
        _domain_request.delete()
        _creator.delete()

    @less_console_noise_decorator
    def test_custom_delete_confirmation_page_table(self):
        """Tests if we override the delete confirmation page for custom content on the table"""
        # Create a ready domain
        domain, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)

        # Get the index. The post expects the index to be encoded as a string
        index = f"{domain.id}"

        # Contains some test tools
        test_helper = GenericTestHelper(
            factory=self.factory,
            user=self.superuser,
            admin=self.admin,
            url=reverse("admin:registrar_domain_changelist"),
            model=Domain,
            client=self.client,
        )

        # Simulate selecting a single record, then clicking "Delete selected domains"
        response = test_helper.get_table_delete_confirmation_page("0", index)

        # Check that our content exists
        content_slice = "When a domain is deleted:"
        self.assertContains(response, content_slice)

    @less_console_noise_decorator
    def test_short_org_name_in_domains_list(self):
        """
        Make sure the short name is displaying in admin on the list page
        """
        domain_request = completed_domain_request(status=DomainRequest.DomainRequestStatus.IN_REVIEW)
        mock_client = MockSESClient()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            domain_request.approve()

        response = self.client.get("/admin/registrar/domain/")
        # There are 4 template references to Federal (4) plus four references in the table
        # for our actual domain_request
        self.assertContains(response, "Federal", count=54)
        self.assertContains(response, "federal", count=225)
        # This may be a bit more robust
        self.assertContains(response, '<td class="field-converted_generic_org_type">Federal</td>', count=1)
        # Now let's make sure the long description does not exist
        self.assertNotContains(response, "Federal: an agency of the U.S. government")

    @override_settings(IS_PRODUCTION=True)
    @less_console_noise_decorator
    def test_prod_only_shows_export(self):
        """Test that production environment only displays export"""
        response = self.client.get("/admin/registrar/domain/")
        self.assertContains(response, ">Export<")
        self.assertNotContains(response, ">Import<")


class TestDomainAdminWebTest(MockEppLib, WebTest):
    """Test DomainAdmin class as super user, using WebTest.
    WebTest allows for easier handling of forms and html responses.

    Notes:
      all tests share superuser; tests must not update superuser
      tests have available superuser, app, and admin
    """

    # csrf checks do not work with WebTest.
    # We disable them here. TODO for another ticket.
    csrf_checks = False

    @classmethod
    def setUpClass(self):
        super().setUpClass()
        self.site = AdminSite()
        self.admin = DomainAdmin(model=Domain, admin_site=self.site)
        self.superuser = create_superuser()
        self.factory = RequestFactory()

    def setUp(self):
        super().setUp()
        self.app.set_user(self.superuser.username)

    def tearDown(self):
        super().tearDown()
        Host.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainRequest.objects.all().delete()

    @classmethod
    def tearDownClass(self):
        User.objects.all().delete()
        super().tearDownClass()

    @less_console_noise_decorator
    @patch("registrar.admin.DomainAdmin._get_current_date", return_value=date(2024, 1, 1))
    def test_extend_expiration_date_button(self, mock_date_today):
        """
        Tests if extend_expiration_date modal gives an accurate date
        """

        # Create a ready domain with a preset expiration date
        domain, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)
        response = self.app.get(reverse("admin:registrar_domain_change", args=[domain.pk]))
        # load expiration date into cache and registrar with below command
        domain.registry_expiration_date
        # Make sure the ex date is what we expect it to be
        domain_ex_date = Domain.objects.get(id=domain.id).expiration_date
        self.assertEqual(domain_ex_date, date(2023, 5, 25))

        # Make sure that the page is loading as expected
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Extend expiration date")

        # Grab the form to submit
        form = response.forms["domain_form"]

        with patch("django.contrib.messages.add_message") as mock_add_message:
            # Submit the form
            response = form.submit("_extend_expiration_date")

            # Follow the response
            response = response.follow()

        # Assert that everything on the page looks correct
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Extend expiration date")

        # Ensure the message we recieve is in line with what we expect
        expected_message = "Successfully extended the expiration date."
        expected_call = call(
            # The WGSI request doesn't need to be tested
            ANY,
            messages.INFO,
            expected_message,
            extra_tags="",
            fail_silently=False,
        )

        mock_add_message.assert_has_calls([expected_call], 1)

    @less_console_noise_decorator
    @patch("registrar.admin.DomainAdmin._get_current_date", return_value=date(2024, 1, 1))
    def test_extend_expiration_date_button_epp(self, mock_date_today):
        """
        Tests if extend_expiration_date button sends the right epp command
        """

        # Create a ready domain with a preset expiration date
        domain, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)

        response = self.app.get(reverse("admin:registrar_domain_change", args=[domain.pk]))

        # Make sure that the page is loading as expected
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Extend expiration date")

        # Grab the form to submit
        form = response.forms["domain_form"]

        with patch("django.contrib.messages.add_message") as mock_add_message:
            with patch("registrar.models.Domain.renew_domain") as renew_mock:
                # Submit the form
                response = form.submit("_extend_expiration_date")

                # Follow the response
                response = response.follow()

        # Assert that it is calling the function with the default extension length.
        # We only need to test the value that EPP sends, as we can assume the other
        # test cases cover the "renew" function.
        renew_mock.assert_has_calls([call()], any_order=False)

        # We should not make duplicate calls
        self.assertEqual(renew_mock.call_count, 1)

        # Assert that everything on the page looks correct
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, domain.name)
        self.assertContains(response, "Extend expiration date")

        # Ensure the message we recieve is in line with what we expect
        expected_message = "Successfully extended the expiration date."
        expected_call = call(
            # The WGSI request doesn't need to be tested
            ANY,
            messages.INFO,
            expected_message,
            extra_tags="",
            fail_silently=False,
        )
        mock_add_message.assert_has_calls([expected_call], 1)

    @less_console_noise_decorator
    def test_custom_delete_confirmation_page(self):
        """Tests if we override the delete confirmation page for custom content"""
        # Create a ready domain with a preset expiration date
        domain, _ = Domain.objects.get_or_create(name="fake.gov", state=Domain.State.READY)

        domain_change_page = self.app.get(reverse("admin:registrar_domain_change", args=[domain.pk]))

        self.assertContains(domain_change_page, "fake.gov")
        # click the "Delete" link
        confirmation_page = domain_change_page.click("Delete", index=0)

        content_slice = "When a domain is deleted:"
        self.assertContains(confirmation_page, content_slice)

    @less_console_noise_decorator
    def test_on_hold_is_successful_web_test(self):
        """
        Scenario: Domain on_hold is successful through webtest
        """
        with less_console_noise():
            domain = create_ready_domain()

            response = self.app.get(reverse("admin:registrar_domain_change", args=[domain.pk]))

            # Check the contents of the modal
            # Check for the header
            self.assertContains(response, "Are you sure you want to place this domain on hold?")

            # Check for some of its body
            self.assertContains(response, "When a domain is on hold:")

            # Check for some of the button content
            self.assertContains(response, "Yes, place hold")

            # Grab the form to submit
            form = response.forms["domain_form"]

            # Submit the form
            response = form.submit("_place_client_hold")

            # Follow the response
            response = response.follow()

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, domain.name)
            self.assertContains(response, "Remove hold")

            # The modal should still exist
            # Check for the header
            self.assertContains(response, "Are you sure you want to place this domain on hold?")

            # Check for some of its body
            self.assertContains(response, "When a domain is on hold:")

            # Check for some of the button content
            self.assertContains(response, "Yes, place hold")

            # Web test has issues grabbing up to date data from the db, so we can test
            # the returned view instead
            self.assertContains(response, '<div class="readonly">On hold</div>')
