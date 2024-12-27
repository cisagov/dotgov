from django.conf import settings


def language_code(request):
    """Add LANGUAGE_CODE to the template context.

    The <html> element of a web page should include a lang="..." attribute. In
    Django, the correct thing to put in that attribute is the value of
    settings.LANGUAGE_CODE but the template context can't access that value
    unless we add it here (and configure this context processor in the
    TEMPLATES dict of our settings file).
    """
    return {"LANGUAGE_CODE": settings.LANGUAGE_CODE}


def canonical_path(request):
    """Add a canonical URL to the template context.

    To make a correct "rel=canonical" link in the HTML page, we need to
    construct an absolute URL for the page, and we can't do that in the
    template itself, so we do it here and pass the information on.
    """
    return {"CANONICAL_PATH": request.build_absolute_uri(request.path)}


def is_demo_site(request):
    """Add a boolean if this is a demo site.

    To be able to render or not our "demo site" banner, we need a context
    variable for the template that indicates if this banner should or
    should not appear.
    """
    return {"IS_DEMO_SITE": settings.IS_DEMO_SITE}


def is_production(request):
    """Add a boolean if this is our production site."""
    return {"IS_PRODUCTION": settings.IS_PRODUCTION}


def org_user_status(request):
    if request.user.is_authenticated:
        is_org_user = request.user.is_org_user(request)
    else:
        is_org_user = False

    return {
        "is_org_user": is_org_user,
    }


def add_path_to_context(request):
    return {"path": getattr(request, "path", None)}


def portfolio_permissions(request):
    """Make portfolio permissions for the request user available in global context"""
    portfolio_context = {
        "has_base_portfolio_permission": False,
        "has_any_domains_portfolio_permission": False,
        "has_any_requests_portfolio_permission": False,
        "has_edit_request_portfolio_permission": False,
        "has_view_suborganization_portfolio_permission": False,
        "has_edit_suborganization_portfolio_permission": False,
        "has_view_members_portfolio_permission": False,
        "has_edit_members_portfolio_permission": False,
        "portfolio": None,
        "has_organization_feature_flag": False,
        "has_organization_requests_flag": False,
        "has_organization_members_flag": False,
        "is_portfolio_admin": False,
        "has_domain_renewal_flag": False,
    }
    try:
        portfolio = request.session.get("portfolio")

        # These feature flags will display and doesn't depend on portfolio
        portfolio_context.update(
            {
                "has_organization_feature_flag": True,
                "has_domain_renewal_flag": request.user.has_domain_renewal_flag(),
            }
        )

        # Linting: line too long
        view_suborg = request.user.has_view_suborganization_portfolio_permission(portfolio)
        edit_suborg = request.user.has_edit_suborganization_portfolio_permission(portfolio)
        if portfolio:
            return {
                "has_base_portfolio_permission": request.user.has_base_portfolio_permission(portfolio),
                "has_edit_request_portfolio_permission": request.user.has_edit_request_portfolio_permission(portfolio),
                "has_view_suborganization_portfolio_permission": view_suborg,
                "has_edit_suborganization_portfolio_permission": edit_suborg,
                "has_any_domains_portfolio_permission": request.user.has_any_domains_portfolio_permission(portfolio),
                "has_any_requests_portfolio_permission": request.user.has_any_requests_portfolio_permission(portfolio),
                "has_view_members_portfolio_permission": request.user.has_view_members_portfolio_permission(portfolio),
                "has_edit_members_portfolio_permission": request.user.has_edit_members_portfolio_permission(portfolio),
                "portfolio": portfolio,
                "has_organization_feature_flag": True,
                "has_organization_requests_flag": request.user.has_organization_requests_flag(),
                "has_organization_members_flag": request.user.has_organization_members_flag(),
                "is_portfolio_admin": request.user.is_portfolio_admin(portfolio),
                "has_domain_renewal_flag": request.user.has_domain_renewal_flag(),
            }
        return portfolio_context

    except AttributeError:
        # Handles cases where request.user might not exist
        return portfolio_context


def is_widescreen_mode(request):
    widescreen_paths = []  # If this list is meant to include specific paths, populate it.
    portfolio_widescreen_paths = [
        "/domains/",
        "/requests/",
        "/request/",
        "/no-organization-requests/",
        "/no-organization-domains/",
        "/domain-request/",
    ]
    # widescreen_paths can be a bear as it trickles down sub-urls. exclude_paths gives us a way out.
    exclude_paths = [
        "/domains/edit",
    ]

    # Check if the current path matches a widescreen path or the root path.
    is_widescreen = any(path in request.path for path in widescreen_paths) or request.path == "/"

    # Check if the user is an organization user and the path matches portfolio paths.
    is_portfolio_widescreen = (
        hasattr(request.user, "is_org_user")
        and request.user.is_org_user(request)
        and any(path in request.path for path in portfolio_widescreen_paths)
        and not any(exclude_path in request.path for exclude_path in exclude_paths)
    )

    # Return a dictionary with the widescreen mode status.
    return {"is_widescreen_mode": is_widescreen or is_portfolio_widescreen}
