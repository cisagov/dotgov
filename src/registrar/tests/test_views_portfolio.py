from django.urls import reverse
from api.tests.common import less_console_noise_decorator
from registrar.config import settings
from registrar.models import Portfolio, SeniorOfficial
from unittest.mock import MagicMock, patch
from django_webtest import WebTest  # type: ignore
from django.core.handlers.wsgi import WSGIRequest
from registrar.models import (
    DomainRequest,
    Domain,
    DomainInformation,
    UserDomainRole,
    User,
    Suborganization,
    AllowedEmail,
)
from registrar.models.domain_invitation import DomainInvitation
from registrar.models.portfolio_invitation import PortfolioInvitation
from registrar.models.user_group import UserGroup
from registrar.models.user_portfolio_permission import UserPortfolioPermission
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from registrar.tests.test_views import TestWithUser
from registrar.utility.email import EmailSendingError
from registrar.utility.errors import MissingEmailError
from .common import MockSESClient, completed_domain_request, create_test_user, create_user
from waffle.testutils import override_flag
from django.contrib.sessions.middleware import SessionMiddleware
import boto3_mocking  # type: ignore
from django.test import Client
import logging
import json

logger = logging.getLogger(__name__)


class TestPortfolio(WebTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = create_test_user()
        self.domain, _ = Domain.objects.get_or_create(name="igorville.gov")
        self.portfolio, _ = Portfolio.objects.get_or_create(creator=self.user, organization_name="Hotel California")
        self.role, _ = UserDomainRole.objects.get_or_create(
            user=self.user, domain=self.domain, role=UserDomainRole.Roles.MANAGER
        )

    def tearDown(self):
        UserPortfolioPermission.objects.all().delete()
        Portfolio.objects.all().delete()
        UserDomainRole.objects.all().delete()
        DomainRequest.objects.all().delete()
        DomainInformation.objects.all().delete()
        Domain.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_portfolio_senior_official(self):
        """Tests that the senior official page on portfolio contains the content we expect"""
        self.app.set_user(self.user.username)

        so = SeniorOfficial.objects.create(
            first_name="Saturn", last_name="Enceladus", title="Planet/Moon", email="spacedivision@igorville.com"
        )

        self.portfolio.senior_official = so
        self.portfolio.save()
        self.portfolio.refresh_from_db()

        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_PORTFOLIO],
        )

        so_portfolio_page = self.app.get(reverse("senior-official"))
        # Assert that we're on the right page
        self.assertContains(so_portfolio_page, "Senior official")
        self.assertContains(so_portfolio_page, "Saturn Enceladus")
        self.assertContains(so_portfolio_page, "Planet/Moon")
        self.assertContains(so_portfolio_page, "spacedivision@igorville.com")
        self.assertNotContains(so_portfolio_page, "Save")

        self.portfolio.delete()
        so.delete()

    @less_console_noise_decorator
    def test_middleware_does_not_redirect_if_no_permission(self):
        """Test that user with no portfolio permission is not redirected when attempting to access home"""
        self.app.set_user(self.user.username)
        UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, additional_permissions=[]
        )
        self.user.portfolio = self.portfolio
        self.user.save()
        self.user.refresh_from_db()
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            portfolio_page = self.app.get(reverse("home"))
            # Assert that we're on the right page
            self.assertNotContains(portfolio_page, self.portfolio.organization_name)

    @less_console_noise_decorator
    def test_middleware_does_not_redirect_if_no_portfolio(self):
        """Test that user with no assigned portfolio is not redirected when attempting to access home"""
        self.app.set_user(self.user.username)
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            portfolio_page = self.app.get(reverse("home"))
            # Assert that we're on the right page
            self.assertNotContains(portfolio_page, self.portfolio.organization_name)

    @less_console_noise_decorator
    def test_middleware_redirects_to_portfolio_no_domains_page(self):
        """Test that user with a portfolio and VIEW_PORTFOLIO is redirected to the no domains page"""
        self.app.set_user(self.user.username)
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_PORTFOLIO],
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            portfolio_page = self.app.get(reverse("home")).follow()
            # Assert that we're on the right page
            self.assertContains(portfolio_page, self.portfolio.organization_name)
            self.assertContains(portfolio_page, '<h1 id="domains-header">Domains</h1>')
            self.assertContains(portfolio_page, "You aren’t managing any domains")

    @less_console_noise_decorator
    def test_middleware_redirects_to_portfolio_domains_page(self):
        """Test that user with a portfolio, VIEW_PORTFOLIO, VIEW_ALL_DOMAINS
        is redirected to portfolio domains page"""
        self.app.set_user(self.user.username)
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS,
            ],
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            portfolio_page = self.app.get(reverse("home")).follow()
            # Assert that we're on the right page
            self.assertContains(portfolio_page, self.portfolio.organization_name)
            self.assertNotContains(portfolio_page, "<h1>Organization</h1>")
            self.assertContains(portfolio_page, '<h1 id="domains-header">Domains</h1>')

    @less_console_noise_decorator
    def test_portfolio_domains_page_403_when_user_not_have_permission(self):
        """Test that user without proper permission is denied access to portfolio domain view"""
        self.app.set_user(self.user.username)
        UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, additional_permissions=[]
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            response = self.app.get(reverse("domains"), status=403)
            # Assert the response is a 403 Forbidden
            self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    def test_portfolio_domain_requests_page_403_when_user_not_have_permission(self):
        """Test that user without proper permission is denied access to portfolio domain view"""
        self.app.set_user(self.user.username)
        UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, additional_permissions=[]
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            response = self.app.get(reverse("domain-requests"), status=403)
            # Assert the response is a 403 Forbidden
            self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    def test_portfolio_organization_page_403_when_user_not_have_permission(self):
        """Test that user without proper permission is not allowed access to portfolio organization page"""
        self.app.set_user(self.user.username)
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, additional_permissions=[]
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            response = self.app.get(reverse("organization"), status=403)
            # Assert the response is a 403 Forbidden
            self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    def test_portfolio_organization_page_read_only(self):
        """Test that user with a portfolio can access the portfolio organization page, read only"""
        self.app.set_user(self.user.username)
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_PORTFOLIO],
        )
        self.portfolio.city = "Los Angeles"
        self.portfolio.save()
        with override_flag("organization_feature", active=True):
            response = self.app.get(reverse("organization"))
            # Assert the response is a 200
            self.assertEqual(response.status_code, 200)
            # The label for Federal agency will always be a h4
            self.assertContains(response, '<h4 class="read-only-label">Organization name</h4>')
            # The read only label for city will be a h4
            self.assertContains(response, '<h4 class="read-only-label">City</h4>')
            self.assertNotContains(response, 'for="id_city"')
            self.assertContains(response, '<p class="read-only-value">Los Angeles</p>')

    @less_console_noise_decorator
    def test_portfolio_organization_page_edit_access(self):
        """Test that user with a portfolio can access the portfolio organization page, read only"""
        self.app.set_user(self.user.username)
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_PORTFOLIO,
            ],
        )
        self.portfolio.city = "Los Angeles"
        self.portfolio.save()
        with override_flag("organization_feature", active=True):
            response = self.app.get(reverse("organization"))
            # Assert the response is a 200
            self.assertEqual(response.status_code, 200)
            # The label for Federal agency will always be a h4
            self.assertContains(response, '<h4 class="read-only-label">Organization name</h4>')
            # The read only label for city will be a h4
            self.assertNotContains(response, '<h4 class="read-only-label">City</h4>')
            self.assertNotContains(response, '<p class="read-only-value">Los Angeles</p>')
            self.assertContains(response, 'for="id_city"')

    @less_console_noise_decorator
    @override_flag("organization_requests", active=True)
    def test_accessible_pages_when_user_does_not_have_permission(self):
        """Tests which pages are accessible when user does not have portfolio permissions"""
        self.app.set_user(self.user.username)
        portfolio_additional_permissions = [
            UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
            UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS,
            UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
        ]
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, additional_permissions=portfolio_additional_permissions
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            portfolio_page = self.app.get(reverse("home")).follow()
            # Assert that we're on the right page
            self.assertContains(portfolio_page, self.portfolio.organization_name)
            self.assertNotContains(portfolio_page, "<h1>Organization</h1>")
            self.assertContains(portfolio_page, '<h1 id="domains-header">Domains</h1>')
            self.assertContains(portfolio_page, reverse("domains"))
            self.assertContains(portfolio_page, reverse("domain-requests"))

            # removing non-basic portfolio perms, which should remove domains
            # and domain requests from nav
            portfolio_permission.additional_permissions = [UserPortfolioPermissionChoices.VIEW_PORTFOLIO]
            portfolio_permission.save()
            portfolio_permission.refresh_from_db()

            # Members should be redirected to the readonly domains page
            portfolio_page = self.app.get(reverse("home")).follow()

            self.assertContains(portfolio_page, self.portfolio.organization_name)
            self.assertNotContains(portfolio_page, "<h1>Organization</h1>")
            self.assertContains(portfolio_page, '<h1 id="domains-header">Domains</h1>')
            self.assertContains(portfolio_page, "You aren’t managing any domains")
            self.assertNotContains(portfolio_page, reverse("domains"))
            self.assertNotContains(portfolio_page, reverse("domain-requests"))

            # The organization page should still be accessible
            org_page = self.app.get(reverse("organization"))
            self.assertContains(org_page, self.portfolio.organization_name)
            self.assertContains(org_page, "<h1>Organization</h1>")

            # Both domain pages should not be accessible
            domain_page = self.app.get(reverse("domains"), expect_errors=True)
            self.assertEquals(domain_page.status_code, 403)
            domain_request_page = self.app.get(reverse("domain-requests"), expect_errors=True)
            self.assertEquals(domain_request_page.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_requests", active=True)
    def test_accessible_pages_when_user_does_not_have_role(self):
        """Test that admin / memmber roles are associated with the right access"""
        self.app.set_user(self.user.username)
        roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, roles=roles
        )
        with override_flag("organization_feature", active=True):
            # This will redirect the user to the portfolio page.
            # Follow implicity checks if our redirect is working.
            portfolio_page = self.app.get(reverse("home")).follow()
            # Assert that we're on the right page
            self.assertContains(portfolio_page, self.portfolio.organization_name)
            self.assertNotContains(portfolio_page, "<h1>Organization</h1>")
            self.assertContains(portfolio_page, '<h1 id="domains-header">Domains</h1>')
            self.assertContains(portfolio_page, reverse("domains"))
            self.assertContains(portfolio_page, reverse("domain-requests"))

            # removing non-basic portfolio role, which should remove domains
            # and domain requests from nav
            portfolio_permission.roles = [UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
            portfolio_permission.save()
            portfolio_permission.refresh_from_db()

            # Members should be redirected to the readonly domains page
            portfolio_page = self.app.get(reverse("home")).follow()

            self.assertContains(portfolio_page, self.portfolio.organization_name)
            self.assertNotContains(portfolio_page, "<h1>Organization</h1>")
            self.assertContains(portfolio_page, '<h1 id="domains-header">Domains</h1>')
            self.assertContains(portfolio_page, "You aren’t managing any domains")
            self.assertNotContains(portfolio_page, reverse("domains"))
            self.assertNotContains(portfolio_page, reverse("domain-requests"))

            # The organization page should still be accessible
            org_page = self.app.get(reverse("organization"))
            self.assertContains(org_page, self.portfolio.organization_name)
            self.assertContains(org_page, "<h1>Organization</h1>")

            # Both domain pages should not be accessible
            domain_page = self.app.get(reverse("domains"), expect_errors=True)
            self.assertEquals(domain_page.status_code, 403)
            domain_request_page = self.app.get(reverse("domain-requests"), expect_errors=True)
            self.assertEquals(domain_request_page.status_code, 403)

    @less_console_noise_decorator
    def test_portfolio_org_name(self):
        """Can load portfolio's org name page."""
        with override_flag("organization_feature", active=True):
            self.app.set_user(self.user.username)
            portfolio_additional_permissions = [
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_PORTFOLIO,
            ]
            portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
                user=self.user, portfolio=self.portfolio, additional_permissions=portfolio_additional_permissions
            )
            page = self.app.get(reverse("organization"))
            self.assertContains(page, "The name of your organization will be publicly listed as the domain registrant.")

    @less_console_noise_decorator
    def test_domain_org_name_address_content(self):
        """Org name and address information appears on the page."""
        with override_flag("organization_feature", active=True):
            self.app.set_user(self.user.username)
            portfolio_additional_permissions = [
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_PORTFOLIO,
            ]
            portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
                user=self.user, portfolio=self.portfolio, additional_permissions=portfolio_additional_permissions
            )

            self.portfolio.organization_name = "Hotel California"
            self.portfolio.save()
            page = self.app.get(reverse("organization"))
            # Once in the sidenav, once in the main nav
            self.assertContains(page, "Hotel California", count=2)
            self.assertContains(page, "Non-Federal Agency")

    @less_console_noise_decorator
    def test_domain_org_name_address_form(self):
        """Submitting changes works on the org name address page."""
        with override_flag("organization_feature", active=True):
            self.app.set_user(self.user.username)
            portfolio_additional_permissions = [
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_PORTFOLIO,
            ]
            portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(
                user=self.user, portfolio=self.portfolio, additional_permissions=portfolio_additional_permissions
            )

            self.portfolio.address_line1 = "1600 Penn Ave"
            self.portfolio.save()
            portfolio_org_name_page = self.app.get(reverse("organization"))
            session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

            portfolio_org_name_page.form["address_line1"] = "6 Downing st"
            portfolio_org_name_page.form["city"] = "London"

            self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
            success_result_page = portfolio_org_name_page.form.submit()
            self.assertEqual(success_result_page.status_code, 200)

            self.assertContains(success_result_page, "6 Downing st")
            self.assertContains(success_result_page, "London")

    @less_console_noise_decorator
    def test_portfolio_in_session_when_organization_feature_active(self):
        """When organization_feature flag is true and user has a portfolio,
        the portfolio should be set in session."""
        self.client.force_login(self.user)
        roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        UserPortfolioPermission.objects.get_or_create(user=self.user, portfolio=self.portfolio, roles=roles)
        with override_flag("organization_feature", active=True):
            response = self.client.get(reverse("home"))
            # Ensure that middleware processes the session
            session_middleware = SessionMiddleware(lambda request: None)
            session_middleware.process_request(response.wsgi_request)
            response.wsgi_request.session.save()
            # Access the session via the request
            session = response.wsgi_request.session
            # Check if the 'portfolio' session variable exists
            self.assertIn("portfolio", session, "Portfolio session variable should exist.")
            # Check the value of the 'portfolio' session variable
            self.assertEqual(session["portfolio"], self.portfolio, "Portfolio session variable has the wrong value.")

    @less_console_noise_decorator
    def test_portfolio_in_session_is_none_when_organization_feature_inactive(self):
        """When organization_feature flag is false and user has a portfolio,
        the portfolio should be set to None in session.
        This test also satisfies the condition when multiple_portfolios flag
        is false and user has a portfolio, so won't add a redundant test for that."""
        self.client.force_login(self.user)
        roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        UserPortfolioPermission.objects.get_or_create(user=self.user, portfolio=self.portfolio, roles=roles)
        response = self.client.get(reverse("home"))
        # Ensure that middleware processes the session
        session_middleware = SessionMiddleware(lambda request: None)
        session_middleware.process_request(response.wsgi_request)
        response.wsgi_request.session.save()
        # Access the session via the request
        session = response.wsgi_request.session
        # Check if the 'portfolio' session variable exists
        self.assertIn("portfolio", session, "Portfolio session variable should exist.")
        # Check the value of the 'portfolio' session variable
        self.assertIsNone(session["portfolio"])

    @less_console_noise_decorator
    def test_portfolio_in_session_is_none_when_organization_feature_active_and_no_portfolio(self):
        """When organization_feature flag is true and user does not have a portfolio,
        the portfolio should be set to None in session."""
        self.client.force_login(self.user)
        with override_flag("organization_feature", active=True):
            response = self.client.get(reverse("home"))
            # Ensure that middleware processes the session
            session_middleware = SessionMiddleware(lambda request: None)
            session_middleware.process_request(response.wsgi_request)
            response.wsgi_request.session.save()
            # Access the session via the request
            session = response.wsgi_request.session
            # Check if the 'portfolio' session variable exists
            self.assertIn("portfolio", session, "Portfolio session variable should exist.")
            # Check the value of the 'portfolio' session variable
            self.assertIsNone(session["portfolio"])

    @less_console_noise_decorator
    def test_portfolio_in_session_when_multiple_portfolios_active(self):
        """When multiple_portfolios flag is true and user has a portfolio,
        the portfolio should be set in session."""
        self.client.force_login(self.user)
        roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        UserPortfolioPermission.objects.get_or_create(user=self.user, portfolio=self.portfolio, roles=roles)
        with override_flag("organization_feature", active=True), override_flag("multiple_portfolios", active=True):
            response = self.client.get(reverse("home"))
            # Ensure that middleware processes the session
            session_middleware = SessionMiddleware(lambda request: None)
            session_middleware.process_request(response.wsgi_request)
            response.wsgi_request.session.save()
            # Access the session via the request
            session = response.wsgi_request.session
            # Check if the 'portfolio' session variable exists
            self.assertIn("portfolio", session, "Portfolio session variable should exist.")
            # Check the value of the 'portfolio' session variable
            self.assertEqual(session["portfolio"], self.portfolio, "Portfolio session variable has the wrong value.")

    @less_console_noise_decorator
    def test_portfolio_in_session_is_none_when_multiple_portfolios_active_and_no_portfolio(self):
        """When multiple_portfolios flag is true and user does not have a portfolio,
        the portfolio should be set to None in session."""
        self.client.force_login(self.user)
        with override_flag("multiple_portfolios", active=True):
            response = self.client.get(reverse("home"))
            # Ensure that middleware processes the session
            session_middleware = SessionMiddleware(lambda request: None)
            session_middleware.process_request(response.wsgi_request)
            response.wsgi_request.session.save()
            # Access the session via the request
            session = response.wsgi_request.session
            # Check if the 'portfolio' session variable exists
            self.assertIn("portfolio", session, "Portfolio session variable should exist.")
            # Check the value of the 'portfolio' session variable
            self.assertIsNone(session["portfolio"])

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_org_member_can_only_see_domains_with_appropriate_permissions(self):
        """A user with the role organization_member should not have access to the domains page
        if they do not have the right permissions.
        """

        permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        )

        # A default organization member should not be able to see any domains
        self.client.force_login(self.user)
        response = self.client.get(reverse("home"), follow=True)

        self.assertFalse(self.user.has_any_domains_portfolio_permission(response.wsgi_request.session.get("portfolio")))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You aren")

        # Test the domains page - this user should not have access
        response = self.client.get(reverse("domains"))
        self.assertEqual(response.status_code, 403)

        # Ensure that this user can see domains with the right permissions
        permission.additional_permissions = [UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS]
        permission.save()
        permission.refresh_from_db()

        # Test the domains page - this user should have access
        response = self.client.get(reverse("domains"))
        self.assertTrue(self.user.has_any_domains_portfolio_permission(response.wsgi_request.session.get("portfolio")))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Domain name")

        # Test the managed domains permission
        permission.additional_permissions = [UserPortfolioPermissionChoices.VIEW_MANAGED_DOMAINS]
        permission.save()
        permission.refresh_from_db()

        # Test the domains page - this user should have access
        response = self.client.get(reverse("domains"))
        self.assertTrue(self.user.has_any_domains_portfolio_permission(response.wsgi_request.session.get("portfolio")))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Domain name")
        permission.delete()

    def check_widescreen_is_loaded(self, page_to_check):
        """Tests if class modifiers for widescreen mode are appropriately loaded into the DOM
        for the given page"""

        self.client.force_login(self.user)

        # Ensure that this user can see domains with the right permissions
        permission, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        )
        permission.additional_permissions = [UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS]
        permission.save()
        permission.refresh_from_db()

        response = self.client.get(reverse(page_to_check))
        # Make sure that the page is loaded correctly
        self.assertEqual(response.status_code, 200)

        # Test for widescreen modifier
        self.assertContains(response, "--widescreen")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_widescreen_css_org_model(self):
        """Tests if class modifiers for widescreen mode are appropriately
        loaded into the DOM for org model pages"""
        self.check_widescreen_is_loaded("domains")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=False)
    def test_widescreen_css_non_org_model(self):
        """Tests if class modifiers for widescreen mode are appropriately
        loaded into the DOM for non-org model pages"""
        self.check_widescreen_is_loaded("home")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=False)
    def test_organization_requests_waffle_flag_off_hides_nav_link_and_restricts_permission(self):
        """Setting the organization_requests waffle off hides the nav link and restricts access to the requests page"""
        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
            ],
        )

        home = self.app.get(reverse("home")).follow()

        self.assertContains(home, "Hotel California")
        self.assertNotContains(home, "Domain requests")

        domain_requests = self.app.get(reverse("domain-requests"), expect_errors=True)
        self.assertEqual(domain_requests.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_organization_requests_waffle_flag_on_shows_nav_link_and_allows_permission(self):
        """Setting the organization_requests waffle on shows the nav link and allows access to the requests page"""
        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
            ],
        )

        home = self.app.get(reverse("home")).follow()

        self.assertContains(home, "Hotel California")
        self.assertContains(home, "Domain requests")

        domain_requests = self.app.get(reverse("domain-requests"))
        self.assertEqual(domain_requests.status_code, 200)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=False)
    def test_organization_members_waffle_flag_off_hides_nav_link(self):
        """Setting the organization_members waffle off hides the nav link"""
        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
            ],
        )

        home = self.app.get(reverse("home")).follow()

        self.assertContains(home, "Hotel California")
        self.assertNotContains(home, "Members")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_organization_members_waffle_flag_on_shows_nav_link(self):
        """Setting the organization_members waffle on shows the nav link"""
        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        home = self.app.get(reverse("home")).follow()

        self.assertContains(home, "Hotel California")
        self.assertContains(home, "Members")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_cannot_view_members_table(self):
        """Test that user without proper permission is denied access to members view."""

        # Users can only view the members table if they have
        # Portfolio Permission "view_members" selected.
        # NOTE: Admins, by default, DO have permission
        # to view/edit members.
        # Scenarios to test include;
        # (1) - User is not admin and can view portfolio, but not the members table
        # (1) - User is admin and can view portfolio, as well as the members table

        # --- non-admin
        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
            ],
        )
        # Verify that the user cannot access the members page
        # This will redirect the user to the members page.
        self.client.force_login(self.user)
        response = self.client.get(reverse("members"), follow=True)
        # Assert the response is a 403 Forbidden
        self.assertEqual(response.status_code, 403)

        # --- admin
        UserPortfolioPermission.objects.filter(user=self.user, portfolio=self.portfolio).update(
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )

        # Admins should have access to this page by default
        response = self.client.get(reverse("members"), follow=True)
        self.assertEqual(response.status_code, 200)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_can_view_members_table(self):
        """Test that user with proper permission is able to access members view"""

        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # Verify that the user can access the members page
        # This will redirect the user to the members page.
        self.client.force_login(self.user)
        response = self.client.get(reverse("members"), follow=True)
        # Make sure the page loaded
        self.assertEqual(response.status_code, 200)

        # ---- Useful debugging stub to see what "assertContains" is finding
        # pattern = r'Members'
        # matches = re.findall(pattern, response.content.decode('utf-8'))
        # for match in matches:
        #     TerminalHelper.colorful_logger(logger.info, TerminalColors.OKCYAN, f"{match}")

        # Make sure the page loaded
        self.assertContains(response, "Members")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_can_manage_members(self):
        """Test that user with proper permission is able to manage members"""
        user = self.user
        self.app.set_user(user.username)

        # give user permissions to view AND manage members
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Give user permissions to modify user objects in the DB
        group, _ = UserGroup.objects.get_or_create(name="full_access_group")
        # Add the user to the group
        user.groups.set([group])

        # Verify that the user can access the members page
        # This will redirect the user to the members page.
        self.client.force_login(self.user)
        response = self.client.get(reverse("members"), follow=True)
        # Make sure the page loaded
        self.assertEqual(response.status_code, 200)

        # Verify that manage settings are sent in the dynamic HTML
        self.client.force_login(self.user)
        response = self.client.get(reverse("get_portfolio_members_json") + f"?portfolio={self.portfolio.pk}")
        self.assertContains(response, '"action_label": "Manage"')
        self.assertContains(response, '"svg_icon": "settings"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_view_only_members(self):
        """Test that user with view only permission settings can only
        view members (not manage them)"""
        user = self.user
        self.app.set_user(user.username)

        # give user permissions to view AND manage members
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )
        # Give user permissions to modify user objects in the DB
        group, _ = UserGroup.objects.get_or_create(name="full_access_group")
        # Add the user to the group
        user.groups.set([group])

        # Verify that the user can access the members page
        # This will redirect the user to the members page.
        self.client.force_login(self.user)
        response = self.client.get(reverse("members"), follow=True)
        # Make sure the page loaded
        self.assertEqual(response.status_code, 200)

        # Verify that view-only settings are sent in the dynamic HTML
        response = self.client.get(reverse("get_portfolio_members_json") + f"?portfolio={self.portfolio.pk}")
        self.assertContains(response, '"action_label": "View"')
        self.assertContains(response, '"svg_icon": "visibility"')

    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_members_admin_detection(self):
        """Test that user with proper permission is able to manage members"""
        user = self.user
        self.app.set_user(user.username)

        # give user permissions to view AND manage members
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Give user permissions to modify user objects in the DB
        group, _ = UserGroup.objects.get_or_create(name="full_access_group")
        # Add the user to the group
        user.groups.set([group])

        # Verify that the user can access the members page
        # This will redirect the user to the members page.
        self.client.force_login(self.user)
        response = self.client.get(reverse("members"), follow=True)
        # Make sure the page loaded
        self.assertEqual(response.status_code, 200)
        # Verify that admin info is sent in the dynamic HTML
        response = self.client.get(reverse("get_portfolio_members_json") + f"?portfolio={self.portfolio.pk}")
        # TerminalHelper.colorful_logger(logger.info, TerminalColors.OKCYAN, f"{response.content}")
        self.assertContains(response, '"is_admin": true')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_cannot_view_member_page_when_flag_is_off(self):
        """Test that user cannot access the member page when waffle flag is off"""

        # Verify that the user cannot access the member page
        self.client.force_login(self.user)
        response = self.client.get(reverse("member", kwargs={"pk": 1}), follow=True)
        # Make sure the page is denied
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_cannot_view_member_page_when_user_has_no_permission(self):
        """Test that user cannot access the member page without proper permission"""

        # give user base permissions
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # Verify that the user cannot access the member page
        self.client.force_login(self.user)
        response = self.client.get(reverse("member", kwargs={"pk": 1}), follow=True)
        # Make sure the page is denied
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_can_view_member_page_when_user_has_view_members(self):
        """Test that user can access the member page with view_members permission"""

        # Arrange
        # give user permissions to view members
        permission_obj, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # Verify the page can be accessed
        self.client.force_login(self.user)
        response = self.client.get(reverse("member", kwargs={"pk": permission_obj.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        # Assert text within the page is correct
        self.assertContains(response, "First Last")
        self.assertContains(response, self.user.email)
        self.assertContains(response, "Basic access")
        self.assertContains(response, "No access")
        self.assertContains(response, "View all members")
        self.assertContains(response, "This member does not manage any domains.")

        # Assert buttons and links within the page are correct
        self.assertNotContains(response, "usa-button--more-actions")  # test that 3 dot is not present
        self.assertNotContains(response, "sprite.svg#edit")  # test that Edit link is not present
        self.assertNotContains(response, "sprite.svg#settings")  # test that Manage link is not present
        self.assertContains(response, "sprite.svg#visibility")  # test that View link is present

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_can_view_member_page_when_user_has_edit_members(self):
        """Test that user can access the member page with edit_members permission"""

        # Arrange
        # give user permissions to view AND manage members
        permission_obj, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Verify the page can be accessed
        self.client.force_login(self.user)
        response = self.client.get(reverse("member", kwargs={"pk": permission_obj.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        # Assert text within the page is correct
        self.assertContains(response, "First Last")
        self.assertContains(response, self.user.email)
        self.assertContains(response, "Admin access")
        self.assertContains(response, "View all requests plus create requests")
        self.assertContains(response, "View all members plus manage members")
        self.assertContains(
            response, 'This member does not manage any domains. To assign this member a domain, click "Manage"'
        )

        # Assert buttons and links within the page are correct
        self.assertContains(response, "wrapper-delete-action")  # test that 3 dot is present
        self.assertContains(response, "sprite.svg#edit")  # test that Edit link is present
        self.assertContains(response, "sprite.svg#settings")  # test that Manage link is present
        self.assertNotContains(response, "sprite.svg#visibility")  # test that View link is not present

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_cannot_view_invitedmember_page_when_flag_is_off(self):
        """Test that user cannot access the invitedmember page when waffle flag is off"""

        # Verify that the user cannot access the member page
        self.client.force_login(self.user)
        response = self.client.get(reverse("invitedmember", kwargs={"pk": 1}), follow=True)
        # Make sure the page is denied
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_cannot_view_invitedmember_page_when_user_has_no_permission(self):
        """Test that user cannot access the invitedmember page without proper permission"""

        # give user base permissions
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # Verify that the user cannot access the member page
        self.client.force_login(self.user)
        response = self.client.get(reverse("invitedmember", kwargs={"pk": 1}), follow=True)
        # Make sure the page is denied
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_can_view_invitedmember_page_when_user_has_view_members(self):
        """Test that user can access the invitedmember page with view_members permission"""

        # Arrange
        # give user permissions to view members
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )
        portfolio_invitation, _ = PortfolioInvitation.objects.get_or_create(
            email="info@example.com",
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # Verify the page can be accessed
        self.client.force_login(self.user)
        response = self.client.get(reverse("invitedmember", kwargs={"pk": portfolio_invitation.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        # Assert text within the page is correct
        self.assertContains(response, "Invited")
        self.assertContains(response, portfolio_invitation.email)
        self.assertContains(response, "Basic access")
        self.assertContains(response, "No access")
        self.assertContains(response, "View all members")
        self.assertContains(response, "This member does not manage any domains.")

        # Assert buttons and links within the page are correct
        self.assertNotContains(response, "usa-button--more-actions")  # test that 3 dot is not present
        self.assertNotContains(response, "sprite.svg#edit")  # test that Edit link is not present
        self.assertNotContains(response, "sprite.svg#settings")  # test that Manage link is not present
        self.assertContains(response, "sprite.svg#visibility")  # test that View link is present

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_can_view_invitedmember_page_when_user_has_edit_members(self):
        """Test that user can access the invitedmember page with edit_members permission"""

        # Arrange
        # give user permissions to view AND manage members
        permission_obj, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        portfolio_invitation, _ = PortfolioInvitation.objects.get_or_create(
            email="info@example.com",
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Verify the page can be accessed
        self.client.force_login(self.user)
        response = self.client.get(reverse("invitedmember", kwargs={"pk": portfolio_invitation.pk}), follow=True)
        self.assertEqual(response.status_code, 200)

        # Assert text within the page is correct
        self.assertContains(response, "Invited")
        self.assertContains(response, portfolio_invitation.email)
        self.assertContains(response, "Admin access")
        self.assertContains(response, "View all requests plus create requests")
        self.assertContains(response, "View all members plus manage members")
        self.assertContains(
            response, 'This member does not manage any domains. To assign this member a domain, click "Manage"'
        )
        # Assert buttons and links within the page are correct
        self.assertContains(response, "wrapper-delete-action")  # test that 3 dot is present
        self.assertContains(response, "sprite.svg#edit")  # test that Edit link is present
        self.assertContains(response, "sprite.svg#settings")  # test that Manage link is present
        self.assertNotContains(response, "sprite.svg#visibility")  # test that View link is not present

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_portfolio_domain_requests_page_when_user_has_no_permissions(self):
        """Test the no requests page"""
        UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        )
        self.client.force_login(self.user)
        # create and submit a domain request
        domain_request = completed_domain_request(user=self.user)
        mock_client = MockSESClient()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            domain_request.submit()
            domain_request.save()

        requests_page = self.client.get(reverse("no-portfolio-requests"), follow=True)

        self.assertContains(requests_page, "You don’t have access to domain requests.")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_main_nav_when_user_has_no_permissions(self):
        """Test the nav contains a link to the no requests page"""
        UserPortfolioPermission.objects.get_or_create(
            user=self.user, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        )
        self.client.force_login(self.user)
        # create and submit a domain request
        domain_request = completed_domain_request(user=self.user)
        mock_client = MockSESClient()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            domain_request.submit()
            domain_request.save()

        portfolio_landing_page = self.client.get(reverse("home"), follow=True)

        # link to no requests
        self.assertContains(portfolio_landing_page, "no-organization-requests/")
        # dropdown
        self.assertNotContains(portfolio_landing_page, "basic-nav-section-two")
        # link to requests
        self.assertNotContains(portfolio_landing_page, 'href="/requests/')
        # link to create
        self.assertNotContains(portfolio_landing_page, 'href="/request/')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_main_nav_when_user_has_all_permissions(self):
        """Test the nav contains a dropdown with a link to create and another link to view requests
        Also test for the existence of the Create a new request btn on the requests page"""
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_REQUESTS],
        )
        self.client.force_login(self.user)
        # create and submit a domain request
        domain_request = completed_domain_request(user=self.user)
        mock_client = MockSESClient()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            domain_request.submit()
            domain_request.save()

        portfolio_landing_page = self.client.get(reverse("home"), follow=True)

        # link to no requests
        self.assertNotContains(portfolio_landing_page, "no-organization-requests/")
        # dropdown
        self.assertContains(portfolio_landing_page, "basic-nav-section-two")
        # link to requests
        self.assertContains(portfolio_landing_page, 'href="/requests/')
        # link to create
        self.assertContains(portfolio_landing_page, 'href="/request/')

        requests_page = self.client.get(reverse("domain-requests"))

        # create new request btn
        self.assertContains(requests_page, "Start a new domain request")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_main_nav_when_user_has_view_but_not_edit_permissions(self):
        """Test the nav contains a simple link to view requests
        Also test for the existence of the Create a new request btn on the requests page"""
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
            ],
        )
        self.client.force_login(self.user)
        # create and submit a domain request
        domain_request = completed_domain_request(user=self.user)
        mock_client = MockSESClient()
        with boto3_mocking.clients.handler_for("sesv2", mock_client):
            domain_request.submit()
            domain_request.save()

        portfolio_landing_page = self.client.get(reverse("home"), follow=True)

        # link to no requests
        self.assertNotContains(portfolio_landing_page, "no-organization-requests/")
        # dropdown
        self.assertNotContains(portfolio_landing_page, "basic-nav-section-two")
        # link to requests
        self.assertContains(portfolio_landing_page, 'href="/requests/')
        # link to create
        self.assertNotContains(portfolio_landing_page, 'href="/request/')

        requests_page = self.client.get(reverse("domain-requests"))

        # create new request btn
        self.assertNotContains(requests_page, "Start a new domain request")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_organization_requests_additional_column(self):
        """The requests table has a column for created at"""
        self.app.set_user(self.user.username)

        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_PORTFOLIO,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
            ],
        )

        home = self.app.get(reverse("home")).follow()

        self.assertContains(home, "Hotel California")
        self.assertContains(home, "Domain requests")

        domain_requests = self.app.get(reverse("domain-requests"))
        self.assertEqual(domain_requests.status_code, 200)

        self.assertContains(domain_requests, "Created by")

    @less_console_noise_decorator
    def test_no_org_requests_no_additional_column(self):
        """The requests table does not have a column for created at"""
        self.app.set_user(self.user.username)

        home = self.app.get(reverse("home"))

        self.assertContains(home, "Domain requests")
        self.assertNotContains(home, "Created by")

    @less_console_noise_decorator
    def test_portfolio_cache_updates_when_modified(self):
        """Test that the portfolio in session updates when the portfolio is modified"""
        self.client.force_login(self.user)
        roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        UserPortfolioPermission.objects.get_or_create(user=self.user, portfolio=self.portfolio, roles=roles)

        with override_flag("organization_feature", active=True):
            # Initial request to set the portfolio in session
            response = self.client.get(reverse("home"), follow=True)

            portfolio = self.client.session.get("portfolio")
            self.assertEqual(portfolio.organization_name, "Hotel California")
            self.assertContains(response, "Hotel California")

            # Modify the portfolio
            self.portfolio.organization_name = "Updated Hotel California"
            self.portfolio.save()

            # Make another request
            response = self.client.get(reverse("home"), follow=True)

            # Check if the updated portfolio name is in the response
            self.assertContains(response, "Updated Hotel California")

            # Verify that the session contains the updated portfolio
            portfolio = self.client.session.get("portfolio")
            self.assertEqual(portfolio.organization_name, "Updated Hotel California")

    @less_console_noise_decorator
    def test_portfolio_cache_updates_when_flag_disabled_while_logged_in(self):
        """Test that the portfolio in session is set to None when the organization_feature flag is disabled"""
        self.client.force_login(self.user)
        roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        UserPortfolioPermission.objects.get_or_create(user=self.user, portfolio=self.portfolio, roles=roles)

        with override_flag("organization_feature", active=True):
            # Initial request to set the portfolio in session
            response = self.client.get(reverse("home"), follow=True)
            portfolio = self.client.session.get("portfolio")
            self.assertEqual(portfolio.organization_name, "Hotel California")
            self.assertContains(response, "Hotel California")

        # Disable the organization_feature flag
        with override_flag("organization_feature", active=False):
            # Make another request
            response = self.client.get(reverse("home"))
            self.assertIsNone(self.client.session.get("portfolio"))
            self.assertNotContains(response, "Hotel California")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_org_user_can_delete_own_domain_request_with_permission(self):
        """Test that an org user with edit permission can delete their own DomainRequest with a deletable status."""

        # Assign the user to a portfolio with edit permission
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_REQUESTS],
        )

        # Create a domain request with status WITHDRAWN
        domain_request = completed_domain_request(
            name="test-domain.gov",
            status=DomainRequest.DomainRequestStatus.WITHDRAWN,
            portfolio=self.portfolio,
        )
        domain_request.creator = self.user
        domain_request.save()

        self.client.force_login(self.user)
        # Perform delete
        response = self.client.post(reverse("domain-request-delete", kwargs={"pk": domain_request.pk}), follow=True)

        # Check that the response is 200
        self.assertEqual(response.status_code, 200)

        # Check that the domain request no longer exists
        self.assertFalse(DomainRequest.objects.filter(pk=domain_request.pk).exists())
        domain_request.delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_delete_domain_request_as_org_user_without_permission_with_deletable_status(self):
        """Test that an org user without edit permission cant delete their DomainRequest even if status is deletable."""

        # Assign the user to a portfolio without edit permission
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[],
        )

        # Create a domain request with status STARTED
        domain_request = completed_domain_request(
            name="test-domain.gov",
            status=DomainRequest.DomainRequestStatus.STARTED,
            portfolio=self.portfolio,
        )
        domain_request.creator = self.user
        domain_request.save()

        self.client.force_login(self.user)
        # Attempt to delete
        response = self.client.post(reverse("domain-request-delete", kwargs={"pk": domain_request.pk}), follow=True)

        # Check response is 403 Forbidden
        self.assertEqual(response.status_code, 403)

        # Check that the domain request still exists
        self.assertTrue(DomainRequest.objects.filter(pk=domain_request.pk).exists())
        domain_request.delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_org_user_cannot_delete_others_domain_requests(self):
        """Test that an org user with edit permission cannot delete DomainRequests they did not create."""

        # Assign the user to a portfolio with edit permission
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_REQUESTS],
        )

        # Create another user and a domain request
        other_user = User.objects.create(username="other_user")
        domain_request = completed_domain_request(
            name="test-domain.gov",
            status=DomainRequest.DomainRequestStatus.STARTED,
            portfolio=self.portfolio,
        )
        domain_request.creator = other_user
        domain_request.save()

        self.client.force_login(self.user)
        # Perform delete as self.user
        response = self.client.post(reverse("domain-request-delete", kwargs={"pk": domain_request.pk}), follow=True)

        # Check response is 403 Forbidden
        self.assertEqual(response.status_code, 403)

        # Check that the domain request still exists
        self.assertTrue(DomainRequest.objects.filter(pk=domain_request.pk).exists())
        domain_request.delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_members_table_contains_hidden_permissions_js_hook(self):
        # In the members_table.html we use data-has-edit-permission as a boolean
        # to indicate if a user has permission to edit members in the specific portfolio

        # 1. User w/ edit permission
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Create a member under same portfolio
        member_email = "a_member@example.com"
        member, _ = User.objects.get_or_create(username="a_member", email=member_email)

        UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # I log in as the User so I can see the Members Table
        self.client.force_login(self.user)

        # Specifically go to the Member Table page
        response = self.client.get(reverse("members"))

        self.assertContains(response, 'data-has-edit-permission="True"')

        # 2. User w/o edit permission (additional permission of EDIT_MEMBERS removed)
        permission = UserPortfolioPermission.objects.get(user=self.user, portfolio=self.portfolio)

        # Remove the EDIT_MEMBERS additional permission
        permission.additional_permissions = [
            perm for perm in permission.additional_permissions if perm != UserPortfolioPermissionChoices.EDIT_MEMBERS
        ]

        # Save the updated permissions list
        permission.save()

        # Re-fetch the page to check for updated permissions
        response = self.client.get(reverse("members"))

        self.assertContains(response, 'data-has-edit-permission="False"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_page_has_kebab_wrapper_for_member_if_user_has_edit_permission(self):
        """Test that the kebab wrapper displays for a member with edit permissions"""

        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Create a member under same portfolio
        member_email = "a_member@example.com"
        member, _ = User.objects.get_or_create(username="a_member", email=member_email)

        upp, _ = UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # I log in as the User so I can see the Manage Member page
        self.client.force_login(self.user)

        # Specifically go to the Manage Member page
        response = self.client.get(reverse("member", args=[upp.id]), follow=True)

        self.assertEqual(response.status_code, 200)

        # Check for email AND member type (which here is just member)
        self.assertContains(response, f'data-member-name="{member_email}"')
        self.assertContains(response, 'data-member-type="member"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_page_has_kebab_wrapper_for_invited_member_if_user_has_edit_permission(self):
        """Test that the kebab wrapper displays for an invitedmember with edit permissions"""

        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Invite a member under same portfolio
        invited_member_email = "invited_member@example.com"
        invitation = PortfolioInvitation.objects.create(
            email=invited_member_email,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # I log in as the User so I can see the Manage Member page
        self.client.force_login(self.user)
        response = self.client.get(reverse("invitedmember", args=[invitation.id]), follow=True)

        self.assertEqual(response.status_code, 200)

        # Assert the invited members email + invitedmember type
        self.assertContains(response, f'data-member-name="{invited_member_email}"')
        self.assertContains(response, 'data-member-type="invitedmember"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_page_does_not_have_kebab_wrapper(self):
        """Test that the kebab does not display."""

        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # That creates a member with only view access
        member_email = "member_with_view_access@example.com"
        member, _ = User.objects.get_or_create(username="test_member_with_view_access", email=member_email)

        upp, _ = UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # I log in as the Member with only view permissions to evaluate the pages behaviour
        # when viewed by someone who doesn't have edit perms
        self.client.force_login(member)

        # Go to the Manage Member page
        response = self.client.get(reverse("member", args=[upp.id]), follow=True)

        self.assertEqual(response.status_code, 200)

        # Assert that the kebab edit options are unavailable
        self.assertNotContains(response, 'data-member-type="member"')
        self.assertNotContains(response, 'data-member-type="invitedmember"')
        self.assertNotContains(response, f'data-member-name="{member_email}"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_page_has_correct_form_wrapper(self):
        """Test that the manage members page the right form wrapper"""

        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # That creates a member
        member_email = "a_member@example.com"
        member, _ = User.objects.get_or_create(email=member_email)

        upp, _ = UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # Login as the User to see the Manage Member page
        self.client.force_login(self.user)

        # Specifically go to the Manage Member page
        response = self.client.get(reverse("member", args=[upp.id]), follow=True)

        # Check for a 200 response
        self.assertEqual(response.status_code, 200)

        # Check for form method + that its "post" and id "member-delete-form"
        self.assertContains(response, "<form")
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'id="member-delete-form"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_toggleable_alert_wrapper_exists_on_members_page(self):
        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # That creates a member
        member_email = "a_member@example.com"
        member, _ = User.objects.get_or_create(email=member_email)

        UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # Login as the User to see the Members Table page
        self.client.force_login(self.user)

        # Specifically go to the Members Table page
        response = self.client.get(reverse("members"))

        # Assert that the toggleable alert ID exists
        self.assertContains(response, '<div id="toggleable-alert"')

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_portfolio_member_delete_view_members_table_active_requests(self):
        """Error state w/ deleting a member with active request on Members Table"""
        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        # That creates a member
        member_email = "a_member@example.com"
        member, _ = User.objects.get_or_create(email=member_email)

        upp, _ = UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        with patch.object(User, "get_active_requests_count_in_portfolio", return_value=1):
            self.client.force_login(self.user)
            # We check X_REQUESTED_WITH bc those return JSON responses
            response = self.client.post(
                reverse("member-delete", kwargs={"pk": upp.pk}), HTTP_X_REQUESTED_WITH="XMLHttpRequest"
            )

            self.assertEqual(response.status_code, 400)  # Bad request due to active requests
            support_url = "https://get.gov/contact/"
            expected_error_message = (
                f"This member has an active domain request and can't be removed from the organization. "
                f"<a href='{support_url}' target='_blank'>Contact the .gov team</a> to remove them."
            )

            self.assertContains(response, expected_error_message, status_code=400)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_portfolio_member_delete_view_members_table_only_admin(self):
        """Error state w/ deleting a member that's the only admin on Members Table"""

        # I'm a user with admin permission
        admin_perm_user, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        with patch.object(User, "is_only_admin_of_portfolio", return_value=True):
            self.client.force_login(self.user)
            # We check X_REQUESTED_WITH bc those return JSON responses
            response = self.client.post(
                reverse("member-delete", kwargs={"pk": admin_perm_user.pk}), HTTP_X_REQUESTED_WITH="XMLHttpRequest"
            )

            self.assertEqual(response.status_code, 400)
            expected_error_message = (
                "There must be at least one admin in your organization. Give another member admin "
                "permissions, make sure they log into the registrar, and then remove this member."
            )
            self.assertContains(response, expected_error_message, status_code=400)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_portfolio_member_table_delete_view_success(self):
        """Success state with deleting on Members Table page bc no active request AND not only admin"""

        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Creating a member that can be deleted (see patch)
        member_email = "deleteable_member@example.com"
        member, _ = User.objects.get_or_create(email=member_email)

        # Set up the member in the portfolio
        upp, _ = UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )

        # And set that the member has no active requests AND it's not the only admin
        with patch.object(User, "get_active_requests_count_in_portfolio", return_value=0), patch.object(
            User, "is_only_admin_of_portfolio", return_value=False
        ):

            # Attempt to delete
            self.client.force_login(self.user)
            response = self.client.post(
                # We check X_REQUESTED_WITH bc those return JSON responses
                reverse("member-delete", kwargs={"pk": upp.pk}),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            # Check for a successful deletion
            self.assertEqual(response.status_code, 200)

            expected_success_message = f"You've removed {member.email} from the organization."
            self.assertContains(response, expected_success_message, status_code=200)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_portfolio_member_delete_view_manage_members_page_active_requests(self):
        """Error state when deleting a member with active requests on the Manage Members page"""

        # I'm an admin user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Create a member with active requests
        member_email = "member_with_active_request@example.com"
        member, _ = User.objects.get_or_create(email=member_email)

        upp, _ = UserPortfolioPermission.objects.get_or_create(
            user=member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        with patch.object(User, "get_active_requests_count_in_portfolio", return_value=1):
            with patch("django.contrib.messages.error") as mock_error:
                self.client.force_login(self.user)
                response = self.client.post(
                    reverse("member-delete", kwargs={"pk": upp.pk}),
                )
                # We don't want to do follow=True in response bc that does automatic redirection

                # We want 302 bc indicates redirect
                self.assertEqual(response.status_code, 302)

                support_url = "https://get.gov/contact/"
                expected_error_message = (
                    f"This member has an active domain request and can't be removed from the organization. "
                    f"<a href='{support_url}' target='_blank'>Contact the .gov team</a> to remove them."
                )

                args, kwargs = mock_error.call_args
                # Check if first arg is a WSGIRequest, confirms request object passed correctly
                # WSGIRequest protocol is basically the HTTPRequest but in Django form (ie POST '/member/1/delete')
                self.assertIsInstance(args[0], WSGIRequest)
                # Check that the error message matches the expected error message
                self.assertEqual(args[1], expected_error_message)

                # Location is used for a 3xx HTTP status code to indicate that the URL was redirected
                # and then confirm that we're still on the Manage Members page
                self.assertEqual(response.headers["Location"], reverse("member", kwargs={"pk": upp.pk}))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_portfolio_member_delete_view_manage_members_page_only_admin(self):
        """Error state when trying to delete the only admin on the Manage Members page"""

        # Create an admin with admin user perms
        admin_perm_user, _ = UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Set them to be the only admin and attempt to delete
        with patch.object(User, "is_only_admin_of_portfolio", return_value=True):
            with patch("django.contrib.messages.error") as mock_error:
                self.client.force_login(self.user)
                response = self.client.post(
                    reverse("member-delete", kwargs={"pk": admin_perm_user.pk}),
                )

                self.assertEqual(response.status_code, 302)

                expected_error_message = (
                    "There must be at least one admin in your organization. Give another member admin "
                    "permissions, make sure they log into the registrar, and then remove this member."
                )

                args, kwargs = mock_error.call_args
                # Check if first arg is a WSGIRequest, confirms request object passed correctly
                # WSGIRequest protocol is basically the HTTPRequest but in Django form (ie POST '/member/1/delete')
                self.assertIsInstance(args[0], WSGIRequest)
                # Check that the error message matches the expected error message
                self.assertEqual(args[1], expected_error_message)

                # Location is used for a 3xx HTTP status code to indicate that the URL was redirected
                # and then confirm that we're still on the Manage Members page
                self.assertEqual(response.headers["Location"], reverse("member", kwargs={"pk": admin_perm_user.pk}))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_portfolio_member_delete_view_manage_members_page_invitedmember(self):
        """Success state w/ deleting invited member on Manage Members page should redirect back to Members Table"""

        # I'm a user
        UserPortfolioPermission.objects.get_or_create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Invite a member under same portfolio
        invited_member_email = "invited_member@example.com"
        invitation = PortfolioInvitation.objects.create(
            email=invited_member_email,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        with patch("django.contrib.messages.success") as mock_success:
            self.client.force_login(self.user)
            response = self.client.post(
                reverse("invitedmember-delete", kwargs={"pk": invitation.pk}),
            )

            self.assertEqual(response.status_code, 302)

            expected_success_message = f"You've removed {invitation.email} from the organization."
            args, kwargs = mock_success.call_args
            # Check if first arg is a WSGIRequest, confirms request object passed correctly
            # WSGIRequest protocol is basically the HTTPRequest but in Django form (ie POST '/member/1/delete')
            self.assertIsInstance(args[0], WSGIRequest)
            # Check that the error message matches the expected error message
            self.assertEqual(args[1], expected_success_message)

            # Location is used for a 3xx HTTP status code to indicate that the URL was redirected
            # and then confirm that we're now on Members Table page
            self.assertEqual(response.headers["Location"], reverse("members"))


class TestPortfolioMemberDomainsView(TestWithUser, WebTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create test member
        cls.user_member = User.objects.create(
            username="test_member",
            first_name="Second",
            last_name="User",
            email="second@example.com",
            phone="8003112345",
            title="Member",
        )

        # Create test user with no perms
        cls.user_no_perms = User.objects.create(
            username="test_user_no_perms",
            first_name="No",
            last_name="Permissions",
            email="user_no_perms@example.com",
            phone="8003112345",
            title="No Permissions",
        )

        # Create Portfolio
        cls.portfolio = Portfolio.objects.create(creator=cls.user, organization_name="Test Portfolio")

        # Assign permissions to the user making requests
        cls.portfolio_permission = UserPortfolioPermission.objects.create(
            user=cls.user,
            portfolio=cls.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        cls.permission = UserPortfolioPermission.objects.create(
            user=cls.user_member,
            portfolio=cls.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

    @classmethod
    def tearDownClass(cls):
        UserPortfolioPermission.objects.all().delete()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        super().tearDownClass()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_authenticated(self):
        """Tests that the portfolio member domains view is accessible."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("member-domains", kwargs={"pk": self.permission.id}))

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user_member.email)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_no_perms(self):
        """Tests that the portfolio member domains view is not accessible to user with no perms."""
        self.client.force_login(self.user_no_perms)

        response = self.client.get(reverse("member-domains", kwargs={"pk": self.permission.id}))

        # Make sure the request returns forbidden
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_unauthenticated(self):
        """Tests that the portfolio member domains view is not accessible when no authenticated user."""
        self.client.logout()

        response = self.client.get(reverse("member-domains", kwargs={"pk": self.permission.id}))

        # Make sure the request returns redirect to openid login
        self.assertEqual(response.status_code, 302)  # Redirect to openid login
        self.assertIn("/openid/login", response.url)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_not_found(self):
        """Tests that the portfolio member domains view returns not found if user portfolio permission not found."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("member-domains", kwargs={"pk": "0"}))

        # Make sure the response is not found
        self.assertEqual(response.status_code, 404)


class TestPortfolioInvitedMemberDomainsView(TestWithUser, WebTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.user_no_perms = User.objects.create(
            username="test_user_no_perms",
            first_name="No",
            last_name="Permissions",
            email="user_no_perms@example.com",
            phone="8003112345",
            title="No Permissions",
        )

        # Create Portfolio
        cls.portfolio = Portfolio.objects.create(creator=cls.user, organization_name="Test Portfolio")

        # Add an invited member who has been invited to manage domains
        cls.invited_member_email = "invited@example.com"
        cls.invitation = PortfolioInvitation.objects.create(
            email=cls.invited_member_email,
            portfolio=cls.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # Assign permissions to the user making requests
        UserPortfolioPermission.objects.create(
            user=cls.user,
            portfolio=cls.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

    @classmethod
    def tearDownClass(cls):
        PortfolioInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        super().tearDownClass()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_invitedmember_domains_authenticated(self):
        """Tests that the portfolio invited member domains view is accessible."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("invitedmember-domains", kwargs={"pk": self.invitation.id}))

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invited_member_email)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_invitedmember_domains_no_perms(self):
        """Tests that the portfolio invited member domains view is not accessible to user with no perms."""
        self.client.force_login(self.user_no_perms)

        response = self.client.get(reverse("invitedmember-domains", kwargs={"pk": self.invitation.id}))

        # Make sure the request returns forbidden
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_invitedmember_domains_unauthenticated(self):
        """Tests that the portfolio invited member domains view is not accessible when no authenticated user."""
        self.client.logout()

        response = self.client.get(reverse("invitedmember-domains", kwargs={"pk": self.invitation.id}))

        # Make sure the request returns redirect to openid login
        self.assertEqual(response.status_code, 302)  # Redirect to openid login
        self.assertIn("/openid/login", response.url)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_not_found(self):
        """Tests that the portfolio invited member domains view returns not found if user is not a member."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("invitedmember-domains", kwargs={"pk": "0"}))

        # Make sure the response is not found
        self.assertEqual(response.status_code, 404)


class TestPortfolioMemberDomainsEditView(TestWithUser, WebTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create Portfolio
        cls.portfolio = Portfolio.objects.create(creator=cls.user, organization_name="Test Portfolio")
        # Create domains for testing
        cls.domain1 = Domain.objects.create(name="1.gov")
        cls.domain2 = Domain.objects.create(name="2.gov")
        cls.domain3 = Domain.objects.create(name="3.gov")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        Domain.objects.all().delete()

    def setUp(self):
        super().setUp()
        # Create test member
        self.user_member = User.objects.create(
            username="test_member",
            first_name="Second",
            last_name="User",
            email="second@example.com",
            phone="8003112345",
            title="Member",
        )
        # Create test user with no perms
        self.user_no_perms = User.objects.create(
            username="test_user_no_perms",
            first_name="No",
            last_name="Permissions",
            email="user_no_perms@example.com",
            phone="8003112345",
            title="No Permissions",
        )
        # Assign permissions to the user making requests
        self.portfolio_permission = UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        # Assign permissions to test member
        self.permission = UserPortfolioPermission.objects.create(
            user=self.user_member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        # Create url to be used in all tests
        self.url = reverse("member-domains-edit", kwargs={"pk": self.portfolio_permission.pk})

    def tearDown(self):
        super().tearDown()
        UserDomainRole.objects.all().delete()
        DomainInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        PortfolioInvitation.objects.all().delete()
        Portfolio.objects.exclude(id=self.portfolio.id).delete()
        User.objects.exclude(id=self.user.id).delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_edit_authenticated(self):
        """Tests that the portfolio member domains edit view is accessible."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("member-domains-edit", kwargs={"pk": self.permission.id}))

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user_member.email)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_edit_no_perms(self):
        """Tests that the portfolio member domains edit view is not accessible to user with no perms."""
        self.client.force_login(self.user_no_perms)

        response = self.client.get(reverse("member-domains-edit", kwargs={"pk": self.permission.id}))

        # Make sure the request returns forbidden
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_edit_unauthenticated(self):
        """Tests that the portfolio member domains edit view is not accessible when no authenticated user."""
        self.client.logout()

        response = self.client.get(reverse("member-domains-edit", kwargs={"pk": self.permission.id}))

        # Make sure the request returns redirect to openid login
        self.assertEqual(response.status_code, 302)  # Redirect to openid login
        self.assertIn("/openid/login", response.url)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_edit_not_found(self):
        """Tests that the portfolio member domains edit view returns not found if user
        portfolio permission not found."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("member-domains-edit", kwargs={"pk": "0"}))

        # Make sure the response is not found
        self.assertEqual(response.status_code, 404)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_with_valid_added_domains(self, mock_send_domain_email):
        """Test that domains can be successfully added."""
        self.client.force_login(self.user)

        data = {
            "added_domains": json.dumps([self.domain1.id, self.domain2.id, self.domain3.id]),  # Mock domain IDs
        }
        response = self.client.post(self.url, data)

        # Check that the UserDomainRole objects were created
        self.assertEqual(UserDomainRole.objects.filter(user=self.user, role=UserDomainRole.Roles.MANAGER).count(), 3)

        # Check for a success message and a redirect
        self.assertRedirects(response, reverse("member-domains", kwargs={"pk": self.portfolio_permission.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(str(messages[0]), "The domain assignment changes have been saved.")

        expected_domains = [self.domain1, self.domain2, self.domain3]
        # Verify that the invitation email was sent
        mock_send_domain_email.assert_called_once()
        call_args = mock_send_domain_email.call_args.kwargs
        self.assertEqual(call_args["email"], "info@example.com")
        self.assertEqual(call_args["requestor"], self.user)
        self.assertEqual(list(call_args["domains"]), list(expected_domains))
        self.assertIsNone(call_args.get("is_member_of_different_org"))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_with_valid_removed_domains(self, mock_send_domain_email):
        """Test that domains can be successfully removed."""
        self.client.force_login(self.user)

        # Create some UserDomainRole objects
        domains = [self.domain1, self.domain2, self.domain3]
        UserDomainRole.objects.bulk_create([UserDomainRole(domain=domain, user=self.user) for domain in domains])

        data = {
            "removed_domains": json.dumps([self.domain1.id, self.domain2.id]),
        }
        response = self.client.post(self.url, data)

        # Check that the UserDomainRole objects were deleted
        self.assertEqual(UserDomainRole.objects.filter(user=self.user).count(), 1)
        self.assertEqual(UserDomainRole.objects.filter(domain=self.domain3, user=self.user).count(), 1)

        # Check for a success message and a redirect
        self.assertRedirects(response, reverse("member-domains", kwargs={"pk": self.portfolio_permission.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(str(messages[0]), "The domain assignment changes have been saved.")
        # assert that send_domain_invitation_email is not called
        mock_send_domain_email.assert_not_called()

        UserDomainRole.objects.all().delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_post_with_invalid_added_domains_data(self):
        """Test that an error is returned for invalid added domains data."""
        self.client.force_login(self.user)

        data = {
            "added_domains": "json-statham",
        }
        response = self.client.post(self.url, data)

        # Check that no UserDomainRole objects were created
        self.assertEqual(UserDomainRole.objects.filter(user=self.user).count(), 0)

        # Check for an error message and a redirect
        self.assertRedirects(response, reverse("member-domains", kwargs={"pk": self.portfolio_permission.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]), "Invalid data for added domains. If the issue persists, please contact help@get.gov."
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_post_with_invalid_removed_domains_data(self):
        """Test that an error is returned for invalid removed domains data."""
        self.client.force_login(self.user)

        data = {
            "removed_domains": "not-a-json",
        }
        response = self.client.post(self.url, data)

        # Check that no UserDomainRole objects were deleted
        self.assertEqual(UserDomainRole.objects.filter(user=self.user).count(), 0)

        # Check for an error message and a redirect
        self.assertRedirects(response, reverse("member-domains", kwargs={"pk": self.portfolio_permission.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]), "Invalid data for removed domains. If the issue persists, please contact help@get.gov."
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_post_with_no_changes(self):
        """Test that no changes message is displayed when no changes are made."""
        self.client.force_login(self.user)

        response = self.client.post(self.url, {})

        # Check that no UserDomainRole objects were created or deleted
        self.assertEqual(UserDomainRole.objects.filter(user=self.user).count(), 0)

        # Check for an info message and a redirect
        self.assertRedirects(response, reverse("member-domains", kwargs={"pk": self.portfolio_permission.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(str(messages[0]), "No changes detected.")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_when_send_domain_email_raises_exception(self, mock_send_domain_email):
        """Test attempt to add new domains when an EmailSendingError raised."""
        self.client.force_login(self.user)

        data = {
            "added_domains": json.dumps([self.domain1.id, self.domain2.id, self.domain3.id]),  # Mock domain IDs
        }
        mock_send_domain_email.side_effect = EmailSendingError("Failed to send email")
        response = self.client.post(self.url, data)

        # Check that the UserDomainRole objects were not created
        self.assertEqual(UserDomainRole.objects.filter(user=self.user, role=UserDomainRole.Roles.MANAGER).count(), 0)

        # Check for an error message and a redirect to edit form
        self.assertRedirects(response, reverse("member-domains-edit", kwargs={"pk": self.portfolio_permission.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]),
            "An unexpected error occurred: Failed to send email. If the issue persists, please contact help@get.gov.",
        )


class TestPortfolioInvitedMemberEditDomainsView(TestWithUser, WebTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create Portfolio
        cls.portfolio = Portfolio.objects.create(creator=cls.user, organization_name="Test Portfolio")
        # Create domains for testing
        cls.domain1 = Domain.objects.create(name="1.gov")
        cls.domain2 = Domain.objects.create(name="2.gov")
        cls.domain3 = Domain.objects.create(name="3.gov")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        Domain.objects.all().delete()

    def setUp(self):
        super().setUp()
        # Add a user with no permissions
        self.user_no_perms = User.objects.create(
            username="test_user_no_perms",
            first_name="No",
            last_name="Permissions",
            email="user_no_perms@example.com",
            phone="8003112345",
            title="No Permissions",
        )
        # Add an invited member who has been invited to manage domains
        self.invited_member_email = "invited@example.com"
        self.invitation = PortfolioInvitation.objects.create(
            email=self.invited_member_email,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # Assign permissions to the user making requests
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        self.url = reverse("invitedmember-domains-edit", kwargs={"pk": self.invitation.pk})

    def tearDown(self):
        super().tearDown()
        Domain.objects.all().delete()
        DomainInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        PortfolioInvitation.objects.all().delete()
        Portfolio.objects.exclude(id=self.portfolio.id).delete()
        User.objects.exclude(id=self.user.id).delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_invitedmember_domains_edit_authenticated(self):
        """Tests that the portfolio invited member domains edit view is accessible."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("invitedmember-domains-edit", kwargs={"pk": self.invitation.id}))

        # Make sure the page loaded, and that we're on the right page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invited_member_email)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_invitedmember_domains_edit_no_perms(self):
        """Tests that the portfolio invited member domains edit view is not accessible to user with no perms."""
        self.client.force_login(self.user_no_perms)

        response = self.client.get(reverse("invitedmember-domains-edit", kwargs={"pk": self.invitation.id}))

        # Make sure the request returns forbidden
        self.assertEqual(response.status_code, 403)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_invitedmember_domains_edit_unauthenticated(self):
        """Tests that the portfolio invited member domains edit view is not accessible when no authenticated user."""
        self.client.logout()

        response = self.client.get(reverse("invitedmember-domains-edit", kwargs={"pk": self.invitation.id}))

        # Make sure the request returns redirect to openid login
        self.assertEqual(response.status_code, 302)  # Redirect to openid login
        self.assertIn("/openid/login", response.url)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_domains_edit_not_found(self):
        """Tests that the portfolio invited member domains edit view returns not found if user is not a member."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("invitedmember-domains-edit", kwargs={"pk": "0"}))

        # Make sure the response is not found
        self.assertEqual(response.status_code, 404)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_with_valid_added_domains(self, mock_send_domain_email):
        """Test adding new domains successfully."""
        self.client.force_login(self.user)

        data = {
            "added_domains": json.dumps([self.domain1.id, self.domain2.id, self.domain3.id]),
        }
        response = self.client.post(self.url, data)

        # Check that the DomainInvitation objects were created
        self.assertEqual(
            DomainInvitation.objects.filter(
                email="invited@example.com", status=DomainInvitation.DomainInvitationStatus.INVITED
            ).count(),
            3,
        )

        # Check for a success message and a redirect
        self.assertRedirects(response, reverse("invitedmember-domains", kwargs={"pk": self.invitation.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(str(messages[0]), "The domain assignment changes have been saved.")

        expected_domains = [self.domain1, self.domain2, self.domain3]
        # Verify that the invitation email was sent
        mock_send_domain_email.assert_called_once()
        call_args = mock_send_domain_email.call_args.kwargs
        self.assertEqual(call_args["email"], "invited@example.com")
        self.assertEqual(call_args["requestor"], self.user)
        self.assertEqual(list(call_args["domains"]), list(expected_domains))
        self.assertFalse(call_args.get("is_member_of_different_org"))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_with_existing_and_new_added_domains(self, _):
        """Test updating existing and adding new invitations."""
        self.client.force_login(self.user)

        # Create existing invitations
        DomainInvitation.objects.bulk_create(
            [
                DomainInvitation(
                    domain=self.domain1,
                    email="invited@example.com",
                    status=DomainInvitation.DomainInvitationStatus.CANCELED,
                ),
                DomainInvitation(
                    domain=self.domain2,
                    email="invited@example.com",
                    status=DomainInvitation.DomainInvitationStatus.INVITED,
                ),
            ]
        )

        data = {
            "added_domains": json.dumps([self.domain1.id, self.domain2.id, self.domain3.id]),
        }
        response = self.client.post(self.url, data)

        # Check that status for domain_id=1 was updated to INVITED
        self.assertEqual(
            DomainInvitation.objects.get(domain=self.domain1, email="invited@example.com").status,
            DomainInvitation.DomainInvitationStatus.INVITED,
        )

        # Check that domain_id=3 was created as INVITED
        self.assertTrue(
            DomainInvitation.objects.filter(
                domain=self.domain3, email="invited@example.com", status=DomainInvitation.DomainInvitationStatus.INVITED
            ).exists()
        )

        # Check for a success message and a redirect
        self.assertRedirects(response, reverse("invitedmember-domains", kwargs={"pk": self.invitation.pk}))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_with_valid_removed_domains(self, mock_send_domain_email):
        """Test removing domains successfully."""
        self.client.force_login(self.user)

        # Create existing invitations
        DomainInvitation.objects.bulk_create(
            [
                DomainInvitation(
                    domain=self.domain1,
                    email="invited@example.com",
                    status=DomainInvitation.DomainInvitationStatus.INVITED,
                ),
                DomainInvitation(
                    domain=self.domain2,
                    email="invited@example.com",
                    status=DomainInvitation.DomainInvitationStatus.INVITED,
                ),
            ]
        )

        data = {
            "removed_domains": json.dumps([self.domain1.id]),
        }
        response = self.client.post(self.url, data)

        # Check that the status for domain_id=1 was updated to CANCELED
        self.assertEqual(
            DomainInvitation.objects.get(domain=self.domain1, email="invited@example.com").status,
            DomainInvitation.DomainInvitationStatus.CANCELED,
        )

        # Check that domain_id=2 remains INVITED
        self.assertEqual(
            DomainInvitation.objects.get(domain=self.domain2, email="invited@example.com").status,
            DomainInvitation.DomainInvitationStatus.INVITED,
        )

        # Check for a success message and a redirect
        self.assertRedirects(response, reverse("invitedmember-domains", kwargs={"pk": self.invitation.pk}))
        # assert that send_domain_invitation_email is not called
        mock_send_domain_email.assert_not_called()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_post_with_invalid_added_domains_data(self):
        """Test handling of invalid JSON for added domains."""
        self.client.force_login(self.user)

        data = {
            "added_domains": "not-a-json",
        }
        response = self.client.post(self.url, data)

        # Check that no DomainInvitation objects were created
        self.assertEqual(DomainInvitation.objects.count(), 0)

        # Check for an error message and a redirect
        self.assertRedirects(response, reverse("invitedmember-domains", kwargs={"pk": self.invitation.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]), "Invalid data for added domains. If the issue persists, please contact help@get.gov."
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_post_with_invalid_removed_domains_data(self):
        """Test handling of invalid JSON for removed domains."""
        self.client.force_login(self.user)

        data = {
            "removed_domains": "json-sudeikis",
        }
        response = self.client.post(self.url, data)

        # Check that no DomainInvitation objects were updated
        self.assertEqual(DomainInvitation.objects.count(), 0)

        # Check for an error message and a redirect
        self.assertRedirects(response, reverse("invitedmember-domains", kwargs={"pk": self.invitation.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]), "Invalid data for removed domains. If the issue persists, please contact help@get.gov."
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_post_with_no_changes(self):
        """Test the case where no changes are made."""
        self.client.force_login(self.user)

        response = self.client.post(self.url, {})

        # Check that no DomainInvitation objects were created or updated
        self.assertEqual(DomainInvitation.objects.count(), 0)

        # Check for an info message and a redirect
        self.assertRedirects(response, reverse("invitedmember-domains", kwargs={"pk": self.invitation.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(str(messages[0]), "No changes detected.")

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_domain_invitation_email")
    def test_post_when_send_domain_email_raises_exception(self, mock_send_domain_email):
        """Test attempt to add new domains when an EmailSendingError raised."""
        self.client.force_login(self.user)

        data = {
            "added_domains": json.dumps([self.domain1.id, self.domain2.id, self.domain3.id]),
        }
        mock_send_domain_email.side_effect = EmailSendingError("Failed to send email")
        response = self.client.post(self.url, data)

        # Check that the DomainInvitation objects were not created
        self.assertEqual(
            DomainInvitation.objects.filter(
                email="invited@example.com", status=DomainInvitation.DomainInvitationStatus.INVITED
            ).count(),
            0,
        )

        # Check for an error message and a redirect to edit form
        self.assertRedirects(response, reverse("invitedmember-domains-edit", kwargs={"pk": self.invitation.pk}))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            str(messages[0]),
            "An unexpected error occurred: Failed to send email. If the issue persists, please contact help@get.gov.",
        )


class TestRequestingEntity(WebTest):
    """The requesting entity page is a domain request form that only exists
    within the context of a portfolio."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = create_user()
        self.portfolio, _ = Portfolio.objects.get_or_create(creator=self.user, organization_name="Hotel California")
        self.portfolio_2, _ = Portfolio.objects.get_or_create(creator=self.user, organization_name="Hotel Alaska")
        self.suborganization, _ = Suborganization.objects.get_or_create(
            name="Rocky road",
            portfolio=self.portfolio,
        )
        self.suborganization_2, _ = Suborganization.objects.get_or_create(
            name="Vanilla",
            portfolio=self.portfolio,
        )
        self.unrelated_suborganization, _ = Suborganization.objects.get_or_create(
            name="Cold",
            portfolio=self.portfolio_2,
        )
        self.portfolio_role = UserPortfolioPermission.objects.create(
            portfolio=self.portfolio,
            user=self.user,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_REQUESTS],
        )
        # Login the current user
        self.app.set_user(self.user.username)

        self.mock_client_class = MagicMock()
        self.mock_client = self.mock_client_class.return_value

    def tearDown(self):
        UserDomainRole.objects.all().delete()
        DomainRequest.objects.all().delete()
        DomainInformation.objects.all().delete()
        Domain.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        Suborganization.objects.all().delete()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_form_validates_duplicate_suborganization(self):
        """Tests that form validation prevents duplicate suborganization names within the same portfolio"""
        # Create an existing suborganization
        suborganization = Suborganization.objects.create(name="Existing Suborg", portfolio=self.portfolio)

        # Start the domain request process
        response = self.app.get(reverse("domain-request:start"))
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]

        # Navigate past the intro page
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        form = response.forms[0]
        response = form.submit().follow()

        # Fill out the requesting entity form
        form = response.forms[0]
        form["portfolio_requesting_entity-requesting_entity_is_suborganization"] = "True"
        form["portfolio_requesting_entity-is_requesting_new_suborganization"] = "True"
        form["portfolio_requesting_entity-requested_suborganization"] = suborganization.name.lower()
        form["portfolio_requesting_entity-suborganization_city"] = "Eggnog"
        form["portfolio_requesting_entity-suborganization_state_territory"] = DomainRequest.StateTerritoryChoices.OHIO

        # Submit form and verify error
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        response = form.submit()
        self.assertContains(response, "This suborganization already exists")

        # Test that a different name is allowed
        form["portfolio_requesting_entity-requested_suborganization"] = "New Suborg"
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        response = form.submit().follow()

        # Verify successful submission by checking we're on the next page
        self.assertContains(response, "Current websites")

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @less_console_noise_decorator
    def test_requesting_entity_page_new_request(self):
        """Tests that the requesting entity page loads correctly when a new request is started"""

        response = self.app.get(reverse("domain-request:start"))

        # Navigate past the intro page
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        intro_form = response.forms[0]
        response = intro_form.submit().follow()

        # Test the requesting entiy page
        self.assertContains(response, "Who will use the domain you’re requesting?")
        self.assertContains(response, "Add suborganization information")
        # We expect to see the portfolio name in two places:
        # the header, and as one of the radio button options.
        self.assertContains(response, self.portfolio.organization_name, count=3)

        # We expect the dropdown list to contain the suborganizations that currently exist on this portfolio
        self.assertContains(response, self.suborganization.name, count=1)
        self.assertContains(response, self.suborganization_2.name, count=1)

        # However, we should only see suborgs that are on the actual portfolio
        self.assertNotContains(response, self.unrelated_suborganization.name)

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @less_console_noise_decorator
    def test_requesting_entity_page_existing_suborg_submission(self):
        """Tests that you can submit a form on this page and set a suborg"""
        response = self.app.get(reverse("domain-request:start"))

        # Navigate past the intro page
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        form = response.forms[0]
        response = form.submit().follow()

        # Check that we're on the right page
        self.assertContains(response, "Who will use the domain you’re requesting?")
        form = response.forms[0]

        # Test selecting an existing suborg
        form["portfolio_requesting_entity-requesting_entity_is_suborganization"] = True
        form["portfolio_requesting_entity-sub_organization"] = f"{self.suborganization.id}"
        form["portfolio_requesting_entity-is_requesting_new_suborganization"] = False

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        response = form.submit().follow()

        # Ensure that the post occurred successfully by checking that we're on the following page.
        self.assertContains(response, "Current websites")
        created_domain_request_exists = DomainRequest.objects.filter(
            organization_name__isnull=True, sub_organization=self.suborganization
        ).exists()
        self.assertTrue(created_domain_request_exists)

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @less_console_noise_decorator
    def test_requesting_entity_page_new_suborg_submission(self):
        """Tests that you can submit a form on this page and set a new suborg"""
        response = self.app.get(reverse("domain-request:start"))

        # Navigate past the intro page
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        form = response.forms[0]
        response = form.submit().follow()

        # Check that we're on the right page
        self.assertContains(response, "Who will use the domain you’re requesting?")
        form = response.forms[0]

        form["portfolio_requesting_entity-requesting_entity_is_suborganization"] = True
        form["portfolio_requesting_entity-is_requesting_new_suborganization"] = True
        form["portfolio_requesting_entity-sub_organization"] = "other"

        form["portfolio_requesting_entity-requested_suborganization"] = "moon"
        form["portfolio_requesting_entity-suborganization_city"] = "kepler"
        form["portfolio_requesting_entity-suborganization_state_territory"] = "AL"

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        response = form.submit().follow()

        # Ensure that the post occurred successfully by checking that we're on the following page.
        self.assertContains(response, "Current websites")
        created_domain_request_exists = DomainRequest.objects.filter(
            organization_name__isnull=True,
            sub_organization__isnull=True,
            requested_suborganization="moon",
            suborganization_city="kepler",
            suborganization_state_territory=DomainRequest.StateTerritoryChoices.ALABAMA,
        ).exists()
        self.assertTrue(created_domain_request_exists)

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @less_console_noise_decorator
    def test_requesting_entity_page_organization_submission(self):
        """Tests submitting an organization on the requesting org form"""
        response = self.app.get(reverse("domain-request:start"))

        # Navigate past the intro page
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        form = response.forms[0]
        response = form.submit().follow()

        # Check that we're on the right page
        self.assertContains(response, "Who will use the domain you’re requesting?")
        form = response.forms[0]

        # Test selecting an existing suborg
        form["portfolio_requesting_entity-requesting_entity_is_suborganization"] = False

        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        response = form.submit().follow()

        # Ensure that the post occurred successfully by checking that we're on the following page.
        self.assertContains(response, "Current websites")
        created_domain_request_exists = DomainRequest.objects.filter(
            organization_name=self.portfolio.organization_name,
        ).exists()
        self.assertTrue(created_domain_request_exists)

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @less_console_noise_decorator
    def test_requesting_entity_page_errors(self):
        """Tests that we get the expected form errors on requesting entity"""
        domain_request = completed_domain_request(user=self.user, portfolio=self.portfolio)
        response = self.app.get(reverse("edit-domain-request", kwargs={"id": domain_request.pk})).follow()
        form = response.forms[0]
        session_id = self.app.cookies[settings.SESSION_COOKIE_NAME]
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        # For 2 the tests below, it is required to submit a form without submitting a value
        # for the select/combobox. WebTest will not do this; by default, WebTest will submit
        # the first choice in a select. So, need to manipulate the form to remove the
        # particular select/combobox that will not be submitted, and then post the form.
        form_action = f"/request/{domain_request.pk}/portfolio_requesting_entity/"

        # Test missing suborganization selection
        form["portfolio_requesting_entity-requesting_entity_is_suborganization"] = True
        form["portfolio_requesting_entity-is_requesting_new_suborganization"] = False
        # remove sub_organization from the form submission
        form_data = form.submit_fields()
        form_data = [(key, value) for key, value in form_data if key != "portfolio_requesting_entity-sub_organization"]
        response = self.app.post(form_action, dict(form_data))
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)
        self.assertContains(response, "Suborganization is required.", status_code=200)

        # Test missing custom suborganization details
        form["portfolio_requesting_entity-requesting_entity_is_suborganization"] = True
        form["portfolio_requesting_entity-is_requesting_new_suborganization"] = True
        form["portfolio_requesting_entity-sub_organization"] = "other"
        # remove suborganization_state_territory from the form submission
        form_data = form.submit_fields()
        form_data = [
            (key, value)
            for key, value in form_data
            if key != "portfolio_requesting_entity-suborganization_state_territory"
        ]
        response = self.app.post(form_action, dict(form_data))
        self.assertContains(response, "Enter the name of your suborganization.", status_code=200)
        self.assertContains(response, "Enter the city where your suborganization is located.", status_code=200)
        self.assertContains(
            response,
            "Select the state, territory, or military post where your suborganization is located.",
            status_code=200,
        )

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_requesting_entity_submission_email_sent(self):
        """Tests that an email is sent out on successful form submission"""
        AllowedEmail.objects.create(email=self.user.email)
        domain_request = completed_domain_request(
            user=self.user,
            # This is the additional details field
            has_anything_else=True,
        )
        domain_request.portfolio = self.portfolio
        domain_request.requested_suborganization = "moon"
        domain_request.suborganization_city = "kepler"
        domain_request.suborganization_state_territory = DomainRequest.StateTerritoryChoices.ALABAMA
        domain_request.save()
        domain_request.refresh_from_db()

        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            domain_request.submit()
        _, kwargs = self.mock_client.send_email.call_args
        body = kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]

        self.assertNotIn("Anything else", body)
        self.assertIn("kepler, AL", body)
        self.assertIn("Requesting entity:", body)
        self.assertIn("Administrators from your organization:", body)

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_requesting_entity_viewonly(self):
        """Tests the review steps page on under our viewonly context"""
        domain_request = completed_domain_request(
            user=create_test_user(),
            # This is the additional details field
            has_anything_else=True,
        )
        domain_request.portfolio = self.portfolio
        domain_request.requested_suborganization = "moon"
        domain_request.suborganization_city = "kepler"
        domain_request.suborganization_state_territory = DomainRequest.StateTerritoryChoices.ALABAMA
        domain_request.save()
        domain_request.refresh_from_db()

        domain_request.submit()

        response = self.app.get(reverse("domain-request-status-viewonly", kwargs={"pk": domain_request.pk}))
        self.assertContains(response, "Requesting entity")
        self.assertContains(response, "moon")
        self.assertContains(response, "kepler, AL")

    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    @boto3_mocking.patching
    @less_console_noise_decorator
    def test_requesting_entity_manage(self):
        """Tests the review steps page on under our manage context"""
        domain_request = completed_domain_request(
            user=self.user,
            # This is the additional details field
            has_anything_else=True,
        )
        domain_request.portfolio = self.portfolio
        domain_request.requested_suborganization = "moon"
        domain_request.suborganization_city = "kepler"
        domain_request.suborganization_state_territory = DomainRequest.StateTerritoryChoices.ALABAMA
        domain_request.save()
        domain_request.refresh_from_db()

        domain_request.submit()

        response = self.app.get(reverse("domain-request-status", kwargs={"pk": domain_request.pk}))
        self.assertContains(response, "Requesting entity")
        self.assertContains(response, "moon")
        self.assertContains(response, "kepler, AL")


class TestPortfolioInviteNewMemberView(TestWithUser, WebTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create Portfolio
        cls.portfolio = Portfolio.objects.create(creator=cls.user, organization_name="Test Portfolio")

        # Add an invited member who has been invited to manage domains
        cls.invited_member_email = "invited@example.com"
        cls.invitation = PortfolioInvitation.objects.create(
            email=cls.invited_member_email,
            portfolio=cls.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        cls.new_member_email = "newmember@example.com"

        AllowedEmail.objects.get_or_create(email=cls.new_member_email)

        # Assign permissions to the user making requests
        UserPortfolioPermission.objects.create(
            user=cls.user,
            portfolio=cls.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

    @classmethod
    def tearDownClass(cls):
        PortfolioInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        AllowedEmail.objects.all().delete()
        super().tearDownClass()

    @boto3_mocking.patching
    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_invite_for_new_users(self):
        """Tests the member invitation flow for new users."""
        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        mock_client_class = MagicMock()
        mock_client = mock_client_class.return_value

        with boto3_mocking.clients.handler_for("sesv2", mock_client_class):
            # Simulate submission of member invite for new user
            final_response = self.client.post(
                reverse("new-member"),
                {
                    "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
                    "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
                    "email": self.new_member_email,
                },
            )

            # Ensure the final submission is successful
            self.assertEqual(final_response.status_code, 302)  # Redirects

            # Validate Database Changes
            # Validate that portfolio invitation was created but not retrieved
            portfolio_invite = PortfolioInvitation.objects.filter(
                email=self.new_member_email, portfolio=self.portfolio
            ).first()
            self.assertIsNotNone(portfolio_invite)
            self.assertEqual(portfolio_invite.email, self.new_member_email)
            self.assertEqual(portfolio_invite.status, PortfolioInvitation.PortfolioInvitationStatus.INVITED)

            # Check that an email was sent
            self.assertTrue(mock_client.send_email.called)

    @boto3_mocking.patching
    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_member_invite_for_new_users_initial_ajax_call_passes(self):
        """Tests the member invitation flow for new users."""
        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        mock_client_class = MagicMock()
        mock_client = mock_client_class.return_value

        with boto3_mocking.clients.handler_for("sesv2", mock_client_class):
            # Simulate submission of member invite for new user
            final_response = self.client.post(
                reverse("new-member"),
                {
                    "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
                    "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
                    "email": self.new_member_email,
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            # Ensure the prep ajax submission is successful
            self.assertEqual(final_response.status_code, 200)

            # Check that the response is a JSON response with is_valid
            json_response = final_response.json()
            self.assertIn("is_valid", json_response)
            self.assertTrue(json_response["is_valid"])

            # assert that portfolio invitation is not created
            self.assertFalse(
                PortfolioInvitation.objects.filter(email=self.new_member_email, portfolio=self.portfolio).exists(),
                "Portfolio invitation should not be created when an Exception occurs.",
            )

            # Check that an email was not sent
            self.assertFalse(mock_client.send_email.called)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_member_invite_for_previously_invited_member_initial_ajax_call_fails(self, mock_send_email):
        """Tests the initial ajax call in the member invitation flow for existing portfolio member."""
        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        invite_count_before = PortfolioInvitation.objects.count()

        # Simulate submission of member invite for user who has already been invited
        response = self.client.post(
            reverse("new-member"),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
                "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
                "email": self.invited_member_email,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)

        # Check that the response is a JSON response with is_valid == False
        json_response = response.json()
        self.assertIn("is_valid", json_response)
        self.assertFalse(json_response["is_valid"])

        # Validate Database has not changed
        invite_count_after = PortfolioInvitation.objects.count()
        self.assertEqual(invite_count_after, invite_count_before)

        # assert that send_portfolio_invitation_email is not called
        mock_send_email.assert_not_called()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_submit_new_member_raises_email_sending_error(self, mock_send_email):
        """Test when adding a new member and email_send method raises EmailSendingError."""
        mock_send_email.side_effect = EmailSendingError("Failed to send email.")

        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        form_data = {
            "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
            "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
            "email": self.new_member_email,
        }

        # Act
        with patch("django.contrib.messages.warning") as mock_warning:
            response = self.client.post(reverse("new-member"), data=form_data)

            # Assert
            # assert that the send_portfolio_invitation_email called
            mock_send_email.assert_called_once_with(
                email=self.new_member_email, requestor=self.user, portfolio=self.portfolio
            )
            # assert that response is a redirect to reverse("members")
            self.assertRedirects(response, reverse("members"))
            # assert that messages contains message, "Could not send email invitation"
            mock_warning.assert_called_once_with(response.wsgi_request, "Could not send email invitation.")
            # assert that portfolio invitation is not created
            self.assertFalse(
                PortfolioInvitation.objects.filter(email=self.new_member_email, portfolio=self.portfolio).exists(),
                "Portfolio invitation should not be created when an EmailSendingError occurs.",
            )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_submit_new_member_raises_missing_email_error(self, mock_send_email):
        """Test when adding a new member and email_send method raises MissingEmailError."""
        mock_send_email.side_effect = MissingEmailError()

        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        form_data = {
            "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
            "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
            "email": self.new_member_email,
        }

        # Act
        with patch("django.contrib.messages.error") as mock_error:
            response = self.client.post(reverse("new-member"), data=form_data)

            # Assert
            # assert that the send_portfolio_invitation_email called
            mock_send_email.assert_called_once_with(
                email=self.new_member_email, requestor=self.user, portfolio=self.portfolio
            )
            # assert that response is a redirect to reverse("members")
            self.assertRedirects(response, reverse("members"))
            # assert that messages contains message, "Could not send email invitation"
            mock_error.assert_called_once_with(
                response.wsgi_request,
                "Can't send invitation email. No email is associated with your user account.",
            )
            # assert that portfolio invitation is not created
            self.assertFalse(
                PortfolioInvitation.objects.filter(email=self.new_member_email, portfolio=self.portfolio).exists(),
                "Portfolio invitation should not be created when a MissingEmailError occurs.",
            )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_submit_new_member_raises_exception(self, mock_send_email):
        """Test when adding a new member and email_send method raises Exception."""
        mock_send_email.side_effect = Exception("Generic exception")

        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        form_data = {
            "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
            "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
            "email": self.new_member_email,
        }

        # Act
        with patch("django.contrib.messages.warning") as mock_warning:
            response = self.client.post(reverse("new-member"), data=form_data)

            # Assert
            # assert that the send_portfolio_invitation_email called
            mock_send_email.assert_called_once_with(
                email=self.new_member_email, requestor=self.user, portfolio=self.portfolio
            )
            # assert that response is a redirect to reverse("members")
            self.assertRedirects(response, reverse("members"))
            # assert that messages contains message, "Could not send email invitation"
            mock_warning.assert_called_once_with(response.wsgi_request, "Could not send email invitation.")
            # assert that portfolio invitation is not created
            self.assertFalse(
                PortfolioInvitation.objects.filter(email=self.new_member_email, portfolio=self.portfolio).exists(),
                "Portfolio invitation should not be created when an Exception occurs.",
            )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_member_invite_for_previously_invited_member(self, mock_send_email):
        """Tests the member invitation flow for existing portfolio member."""
        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        invite_count_before = PortfolioInvitation.objects.count()

        # Simulate submission of member invite for user who has already been invited
        response = self.client.post(
            reverse("new-member"),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
                "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
                "email": self.invited_member_email,
            },
        )
        self.assertEqual(response.status_code, 200)

        # verify messages
        self.assertContains(
            response,
            (
                "This user is already assigned to a portfolio invitation. "
                "Based on current waffle flag settings, users cannot be assigned "
                "to multiple portfolios."
            ),
        )

        # Validate Database has not changed
        invite_count_after = PortfolioInvitation.objects.count()
        self.assertEqual(invite_count_after, invite_count_before)

        # assert that send_portfolio_invitation_email is not called
        mock_send_email.assert_not_called()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_member_invite_for_existing_member(self, mock_send_email):
        """Tests the member invitation flow for existing portfolio member."""
        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        invite_count_before = PortfolioInvitation.objects.count()

        # Simulate submission of member invite for user who has already been invited
        response = self.client.post(
            reverse("new-member"),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
                "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
                "email": self.user.email,
            },
        )
        self.assertEqual(response.status_code, 200)

        # Verify messages
        self.assertContains(
            response,
            (
                "This user is already assigned to a portfolio. "
                "Based on current waffle flag settings, users cannot be "
                "assigned to multiple portfolios."
            ),
        )

        # Validate Database has not changed
        invite_count_after = PortfolioInvitation.objects.count()
        self.assertEqual(invite_count_after, invite_count_before)

        # assert that send_portfolio_invitation_email is not called
        mock_send_email.assert_not_called()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @patch("registrar.views.portfolios.send_portfolio_invitation_email")
    def test_member_invite_for_existing_user_who_is_not_a_member(self, mock_send_email):
        """Tests the member invitation flow for existing user who is not a portfolio member."""
        self.client.force_login(self.user)

        # Simulate a session to ensure continuity
        session_id = self.client.session.session_key
        self.app.set_cookie(settings.SESSION_COOKIE_NAME, session_id)

        new_user = User.objects.create(email="newuser@example.com")

        # Simulate submission of member invite for the newly created user
        response = self.client.post(
            reverse("new-member"),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value,
                "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value,
                "email": "newuser@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)

        # Validate Database Changes
        # Validate that portfolio invitation was created and retrieved
        portfolio_invite = PortfolioInvitation.objects.filter(
            email="newuser@example.com", portfolio=self.portfolio
        ).first()
        self.assertIsNotNone(portfolio_invite)
        self.assertEqual(portfolio_invite.email, "newuser@example.com")
        self.assertEqual(portfolio_invite.status, PortfolioInvitation.PortfolioInvitationStatus.RETRIEVED)
        # Validate UserPortfolioPermission
        user_portfolio_permission = UserPortfolioPermission.objects.filter(
            user=new_user, portfolio=self.portfolio
        ).first()
        self.assertIsNotNone(user_portfolio_permission)

        # assert that send_portfolio_invitation_email is called
        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args.kwargs
        self.assertEqual(call_args["email"], "newuser@example.com")
        self.assertEqual(call_args["requestor"], self.user)
        self.assertIsNone(call_args.get("is_member_of_different_org"))


class TestEditPortfolioMemberView(WebTest):
    """Tests for the edit member page on portfolios"""

    def setUp(self):
        self.user = create_user()
        # Create Portfolio
        self.portfolio = Portfolio.objects.create(creator=self.user, organization_name="Test Portfolio")

        # Add an invited member who has been invited to manage domains
        self.invited_member_email = "invited@example.com"
        self.invitation = PortfolioInvitation.objects.create(
            email=self.invited_member_email,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
            ],
        )

        # Assign permissions to the user making requests
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

    def tearDown(self):
        PortfolioInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        Portfolio.objects.all().delete()
        User.objects.all().delete()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_edit_member_permissions_basic_to_admin(self):
        """Tests converting a basic member to admin with full permissions."""
        self.client.force_login(self.user)

        # Create a basic member to edit
        basic_member = create_test_user()
        basic_permission = UserPortfolioPermission.objects.create(
            user=basic_member,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS],
        )

        response = self.client.post(
            reverse("member-permissions", kwargs={"pk": basic_permission.id}),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
                "domain_request_permission_admin": UserPortfolioPermissionChoices.EDIT_REQUESTS,
                "member_permission_admin": UserPortfolioPermissionChoices.EDIT_MEMBERS,
            },
        )

        # Verify redirect and success message
        self.assertEqual(response.status_code, 302)

        # Verify database changes
        basic_permission.refresh_from_db()
        self.assertEqual(basic_permission.roles, [UserPortfolioRoleChoices.ORGANIZATION_ADMIN])
        self.assertEqual(
            set(basic_permission.additional_permissions),
            {
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            },
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_edit_member_permissions_validation(self):
        """Tests form validation for required fields based on role."""
        self.client.force_login(self.user)

        member = create_test_user()
        permission = UserPortfolioPermission.objects.create(
            user=member, portfolio=self.portfolio, roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        )

        # Test missing required admin permissions
        response = self.client.post(
            reverse("member-permissions", kwargs={"pk": permission.id}),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
                # Missing required admin fields
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].errors["domain_request_permission_admin"][0],
            "Admin domain request permission is required",
        )
        self.assertEqual(
            response.context["form"].errors["member_permission_admin"][0], "Admin member permission is required"
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_edit_invited_member_permissions(self):
        """Tests editing permissions for an invited (but not yet joined) member."""
        self.client.force_login(self.user)

        # Test updating invitation permissions
        response = self.client.post(
            reverse("invitedmember-permissions", kwargs={"pk": self.invitation.id}),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
                "domain_request_permission_admin": UserPortfolioPermissionChoices.EDIT_REQUESTS,
                "member_permission_admin": UserPortfolioPermissionChoices.EDIT_MEMBERS,
            },
        )

        self.assertEqual(response.status_code, 302)

        # Verify invitation was updated
        updated_invitation = PortfolioInvitation.objects.get(pk=self.invitation.id)
        self.assertEqual(updated_invitation.roles, [UserPortfolioRoleChoices.ORGANIZATION_ADMIN])
        self.assertEqual(
            set(updated_invitation.additional_permissions),
            {
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            },
        )

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_admin_removing_own_admin_role(self):
        """Tests an admin removing their own admin role redirects to home.

        Removing the admin role will remove both view and edit members permissions.
        Note: The user can remove the edit members permissions but as long as they
        stay in admin role, they will at least still have view members permissions.
        """

        self.client.force_login(self.user)

        # Get the user's admin permission
        admin_permission = UserPortfolioPermission.objects.get(user=self.user, portfolio=self.portfolio)

        response = self.client.post(
            reverse("member-permissions", kwargs={"pk": admin_permission.id}),
            {
                "role": UserPortfolioRoleChoices.ORGANIZATION_MEMBER,
                "domain_request_permission_member": UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))
