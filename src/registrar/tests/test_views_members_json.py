from django.urls import reverse

from api.tests.common import less_console_noise_decorator
from registrar.models.domain import Domain
from registrar.models.domain_information import DomainInformation
from registrar.models.domain_invitation import DomainInvitation
from registrar.models.portfolio import Portfolio
from registrar.models.portfolio_invitation import PortfolioInvitation
from registrar.models.user import User
from registrar.models.user_domain_role import UserDomainRole
from registrar.models.user_portfolio_permission import UserPortfolioPermission
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from waffle.testutils import override_flag
from registrar.tests.common import MockEppLib, create_test_user
from django_webtest import WebTest  # type: ignore


class GetPortfolioMembersJsonTest(MockEppLib, WebTest):
    def setUp(self):
        super().setUp()
        self.user = create_test_user()

        # Create additional users
        self.user2 = User.objects.create(
            username="test_user2",
            first_name="Second",
            last_name="User",
            email="second@example.com",
            phone="8003112345",
            title="Member",
        )
        self.user3 = User.objects.create(
            username="test_user3",
            first_name="Third",
            last_name="User",
            email="third@example.com",
            phone="8003113456",
            title="Member",
        )
        self.user4 = User.objects.create(
            username="test_user4",
            first_name="Fourth",
            last_name="User",
            email="fourth@example.com",
            phone="8003114567",
            title="Admin",
        )
        self.user5 = User.objects.create(
            username="test_user5",
            first_name="Fifth",
            last_name="User",
            email="fifth@example.com",
            phone="8003114568",
            title="Admin",
        )
        self.email6 = "fifth@example.com"
        self.email7 = "sixth@example.com"

        # Create Portfolio
        self.portfolio = Portfolio.objects.create(creator=self.user, organization_name="Test Portfolio")

        # Assign permissions

        self.app.set_user(self.user.username)

    def tearDown(self):
        UserDomainRole.objects.all().delete()
        DomainInformation.objects.all().delete()
        Domain.objects.all().delete()
        PortfolioInvitation.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        Portfolio.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_get_portfolio_members_json_authenticated(self):
        """Test that portfolio members are returned properly for an authenticated user."""
        """Also tests that reposnse is 200 when no domains"""
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        UserPortfolioPermission.objects.create(
            user=self.user2,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user3,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user4,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )
        UserPortfolioPermission.objects.create(
            user=self.user5,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )

        response = self.app.get(reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id})
        self.assertEqual(response.status_code, 200)
        data = response.json

        # Check pagination info
        self.assertEqual(data["page"], 1)
        self.assertFalse(data["has_previous"])
        self.assertFalse(data["has_next"])
        self.assertEqual(data["num_pages"], 1)
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["unfiltered_total"], 5)

        # Check the number of members
        self.assertEqual(len(data["members"]), 5)

        # Check member fields
        expected_emails = {
            self.user.email,
            self.user2.email,
            self.user3.email,
            self.user4.email,
            self.user4.email,
            self.user5.email,
        }
        actual_emails = {member["email"] for member in data["members"]}
        self.assertEqual(expected_emails, actual_emails)

        expected_roles = {
            UserPortfolioRoleChoices.ORGANIZATION_MEMBER,
            UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
        }
        # Convert each member's roles list to a frozenset
        actual_roles = {role for member in data["members"] for role in member["roles"]}
        self.assertEqual(expected_roles, actual_roles)

        # Assert that the expected additional permissions are in the actual entire permissions list
        expected_additional_permissions = {
            UserPortfolioPermissionChoices.VIEW_MEMBERS,
            UserPortfolioPermissionChoices.EDIT_MEMBERS,
        }
        # actual_permissions includes additional permissions as well as permissions from roles
        actual_permissions = {permission for member in data["members"] for permission in member["permissions"]}
        self.assertTrue(expected_additional_permissions.issubset(actual_permissions))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_get_portfolio_invited_json_authenticated(self):
        """Test that portfolio invitees are returned properly for an authenticated user."""
        """Also tests that response is 200 when no domains"""
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        PortfolioInvitation.objects.create(
            email=self.email6,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        response = self.app.get(reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id})
        self.assertEqual(response.status_code, 200)
        data = response.json

        # Check pagination info
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["num_pages"], 1)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["unfiltered_total"], 2)

        # Check the number of members
        self.assertEqual(len(data["members"]), 2)

        # Check member fields
        expected_emails = {self.user.email, self.email6}
        actual_emails = {member["email"] for member in data["members"]}
        self.assertEqual(expected_emails, actual_emails)

        expected_roles = {
            UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
        }
        # Convert each member's roles list to a frozenset
        actual_roles = {role for member in data["members"] for role in member["roles"]}
        self.assertEqual(expected_roles, actual_roles)

        expected_additional_permissions = {
            UserPortfolioPermissionChoices.VIEW_MEMBERS,
            UserPortfolioPermissionChoices.EDIT_MEMBERS,
        }
        actual_additional_permissions = {
            permission for member in data["members"] for permission in member["permissions"]
        }
        self.assertTrue(expected_additional_permissions.issubset(actual_additional_permissions))

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_get_portfolio_members_json_with_domains(self):
        """Test that portfolio members are returned properly for an authenticated user and the response includes
        the domains that the member manages.."""
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        UserPortfolioPermission.objects.create(
            user=self.user2,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user3,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user4,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )

        # create domain for which user is manager and domain in portfolio
        domain = Domain.objects.create(
            name="somedomain1.com",
        )
        DomainInformation.objects.create(
            creator=self.user,
            domain=domain,
            portfolio=self.portfolio,
        )
        UserDomainRole.objects.create(
            user=self.user,
            domain=domain,
            role=UserDomainRole.Roles.MANAGER,
        )

        # create another domain in the portfolio
        domain2 = Domain.objects.create(
            name="thissecondpermtestsmultipleperms@lets.notbreak",
        )
        DomainInformation.objects.create(
            creator=self.user,
            domain=domain2,
            portfolio=self.portfolio,
        )
        UserDomainRole.objects.create(
            user=self.user,
            domain=domain2,
            role=UserDomainRole.Roles.MANAGER,
        )

        # create domain for which user is manager and domain not in portfolio
        domain3 = Domain.objects.create(
            name="somedomain3.com",
        )
        DomainInformation.objects.create(
            creator=self.user,
            domain=domain3,
        )
        UserDomainRole.objects.create(
            user=self.user,
            domain=domain3,
            role=UserDomainRole.Roles.MANAGER,
        )

        response = self.app.get(reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id})
        self.assertEqual(response.status_code, 200)
        data = response.json

        # Check if the domain appears in the response JSON and that domain2 does not
        domain_names = [domain_name for member in data["members"] for domain_name in member.get("domain_names", [])]
        self.assertIn("somedomain1.com", domain_names)
        self.assertIn("thissecondpermtestsmultipleperms@lets.notbreak", domain_names)
        self.assertNotIn("somedomain3.com", domain_names)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_get_portfolio_invited_json_with_domains(self):
        """Test that portfolio invited members are returned properly for an authenticated user and the response includes
        the domains that the member manages. Test also verifies that retrieved invitations are not included."""
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_MEMBERS],
        )

        PortfolioInvitation.objects.create(
            email=self.email6,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        PortfolioInvitation.objects.create(
            email=self.email7,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
            status=PortfolioInvitation.PortfolioInvitationStatus.RETRIEVED,
        )

        # create a domain in the portfolio
        domain = Domain.objects.create(
            name="somedomain1.com",
        )
        DomainInformation.objects.create(
            creator=self.user,
            domain=domain,
            portfolio=self.portfolio,
        )
        DomainInvitation.objects.create(
            email=self.email6,
            domain=domain,
        )

        # create another domain in the portfolio
        domain2 = Domain.objects.create(
            name="thissecondinvitetestsasubqueryinjson@lets.notbreak",
        )
        DomainInformation.objects.create(
            creator=self.user,
            domain=domain2,
            portfolio=self.portfolio,
        )
        DomainInvitation.objects.create(
            email=self.email6,
            domain=domain2,
        )

        # create a domain not in the portfolio
        domain3 = Domain.objects.create(
            name="somedomain3.com",
        )
        DomainInformation.objects.create(
            creator=self.user,
            domain=domain3,
        )
        DomainInvitation.objects.create(
            email=self.email6,
            domain=domain3,
        )

        response = self.app.get(reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id})
        self.assertEqual(response.status_code, 200)
        data = response.json

        # Check if the domain appears in the response JSON and domain2 does not
        domain_names = [domain_name for member in data["members"] for domain_name in member.get("domain_names", [])]
        self.assertIn("somedomain1.com", domain_names)
        self.assertIn("thissecondinvitetestsasubqueryinjson@lets.notbreak", domain_names)
        self.assertNotIn("somedomain3.com", domain_names)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_pagination(self):
        """Test that pagination works properly when there are more members than page size."""
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        UserPortfolioPermission.objects.create(
            user=self.user2,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user3,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user4,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )
        PortfolioInvitation.objects.create(
            email=self.email6,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Create additional members to exceed page size of 10
        for i in range(6, 16):
            user, _ = User.objects.get_or_create(
                username=f"test_user{i}",
                first_name=f"User{i}",
                last_name=f"Last{i}",
                email=f"user{i}@example.com",
                phone=f"80031156{i}",
                title="Member",
            )
            UserPortfolioPermission.objects.create(
                user=user,
                portfolio=self.portfolio,
                roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            )

        response = self.app.get(
            reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id, "page": 1}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json

        # Check pagination info
        self.assertEqual(data["page"], 1)
        self.assertTrue(data["has_next"])
        self.assertFalse(data["has_previous"])
        self.assertEqual(data["num_pages"], 2)
        self.assertEqual(data["total"], 15)
        self.assertEqual(data["unfiltered_total"], 15)

        # Check the number of members on page 1
        self.assertEqual(len(data["members"]), 10)

        response = self.app.get(
            reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id, "page": 2}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json

        # Check pagination info for page 2
        self.assertEqual(data["page"], 2)
        self.assertFalse(data["has_next"])
        self.assertTrue(data["has_previous"])
        self.assertEqual(data["num_pages"], 2)

        # Check the number of members on page 2
        self.assertEqual(len(data["members"]), 5)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    def test_search(self):
        """Test search functionality for portfolio members."""
        UserPortfolioPermission.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )
        UserPortfolioPermission.objects.create(
            user=self.user2,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user3,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
        )
        UserPortfolioPermission.objects.create(
            user=self.user4,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )
        PortfolioInvitation.objects.create(
            email=self.email6,
            portfolio=self.portfolio,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
            ],
        )

        # Search by name
        response = self.app.get(
            reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id, "search_term": "Second"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(len(data["members"]), 1)
        self.assertEqual(data["members"][0]["name"], "Second User")
        self.assertEqual(data["members"][0]["email"], "second@example.com")

        # Search by email
        response = self.app.get(
            reverse("get_portfolio_members_json"),
            params={"portfolio": self.portfolio.id, "search_term": "fourth@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(len(data["members"]), 1)
        self.assertEqual(data["members"][0]["email"], "fourth@example.com")

        # Search with no matching results
        response = self.app.get(
            reverse("get_portfolio_members_json"), params={"portfolio": self.portfolio.id, "search_term": "NonExistent"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(len(data["members"]), 0)
