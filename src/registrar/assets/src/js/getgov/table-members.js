import { hideElement, showElement, getCsrfToken } from './helpers.js';
import { uswdsInitializeModals, uswdsUnloadModals } from './helpers-uswds.js';
import { BaseTable, addModal, generateKebabHTML } from './table-base.js';

export class MembersTable extends BaseTable {

  constructor() {
    super('member');
  }

  getBaseUrl() {
    return document.getElementById("get_members_json_url");
  }

  // Abstract method (to be implemented in the child class)
  getDataObjects(data) {
    return data.members;
  }
  unloadModals() {
    uswdsUnloadModals();
  }
  loadModals(page, total, unfiltered_total) {
    // initialize modals immediately after the DOM content is updated
    uswdsInitializeModals();

    // Now the DOM and modals are ready, add listeners to the submit buttons
    const modals = document.querySelectorAll('.usa-modal__content');

    modals.forEach(modal => {
      const submitButton = modal.querySelector('.usa-modal__submit');
      const closeButton = modal.querySelector('.usa-modal__close');
      submitButton.addEventListener('click', () => {
        let pk = submitButton.getAttribute('data-pk');
        // Close the modal to remove the USWDS UI local classes
        closeButton.click();
        // If we're deleting the last item on a page that is not page 1, we'll need to refresh the display to the previous page
        let pageToDisplay = page;
        if (total == 1 && unfiltered_total > 1) {
          pageToDisplay--;
        }

        this.deleteMember(pk, pageToDisplay);
      });
    });
  }

  customizeTable(data) {
    // Get whether the logged in user has edit members permission
    const hasEditPermission = this.portfolioElement ? this.portfolioElement.getAttribute('data-has-edit-permission')==='True' : null;

    let existingExtraActionsHeader =  document.querySelector('.extra-actions-header');

    if (hasEditPermission && !existingExtraActionsHeader) {
      const extraActionsHeader = document.createElement('th');
      extraActionsHeader.setAttribute('id', 'extra-actions');
      extraActionsHeader.setAttribute('role', 'columnheader');
      extraActionsHeader.setAttribute('class', 'extra-actions-header width-5');
      extraActionsHeader.innerHTML = `
        <span class="usa-sr-only">Extra Actions</span>`;
      let tableHeaderRow = this.tableWrapper.querySelector('thead tr');
      tableHeaderRow.appendChild(extraActionsHeader);
    }
    return { 
      'hasAdditionalActions': hasEditPermission,
      'UserPortfolioPermissionChoices' : data.UserPortfolioPermissionChoices
    };
  }

  addRow(dataObject, tbody, customTableOptions) {
    const member = dataObject;
    // member is based on either a UserPortfolioPermission or a PortfolioInvitation
    // and also includes information from related domains; the 'id' of the org_member
    // is the id of the UserPorfolioPermission or PortfolioInvitation, it is not a user id
    // member.type is either invitedmember or member
    const unique_id = member.type + member.id; // unique string for use in dom, this is
    // not the id of the associated user
    const member_delete_url = member.action_url + "/delete";
    const num_domains = member.domain_urls.length;
    const last_active = this.handleLastActive(member.last_active);
    let cancelInvitationButton = member.type === "invitedmember" ? "Cancel invitation" : "Remove member";
    const kebabHTML = customTableOptions.hasAdditionalActions ? generateKebabHTML('remove-member', unique_id, cancelInvitationButton, `for ${member.name}`): ''; 

    const row = document.createElement('tr');

    let admin_tagHTML = ``;
    if (member.is_admin)
      admin_tagHTML = `<span class="usa-tag margin-left-1 bg-primary">Admin</span>`

    // generate html blocks for domains and permissions for the member
    let domainsHTML = this.generateDomainsHTML(num_domains, member.domain_names, member.domain_urls, member.action_url);
    let permissionsHTML = this.generatePermissionsHTML(member.permissions, customTableOptions.UserPortfolioPermissionChoices);

    // domainsHTML block and permissionsHTML block need to be wrapped with hide/show toggle, Expand
    let showMoreButton = '';
    const showMoreRow = document.createElement('tr');
    if (domainsHTML || permissionsHTML) {
      showMoreButton = `
        <button 
          type="button" 
          class="usa-button--show-more-button usa-button usa-button--unstyled display-block margin-top-1" 
          data-for=${unique_id}
          aria-label="Expand for additional information"
        >
          <span>Expand</span>
          <svg class="usa-icon usa-icon--large" aria-hidden="true" focusable="false" role="img" width="24">
            <use xlink:href="/public/img/sprite.svg#expand_more"></use>
          </svg>
        </button>
      `;

      showMoreRow.innerHTML = `<td colspan='3' headers="header-member row-header-${unique_id}" class="padding-top-0"><div class='grid-row'>${domainsHTML} ${permissionsHTML}</div></td>`;
      showMoreRow.classList.add('show-more-content');
      showMoreRow.classList.add('display-none');
      showMoreRow.id = unique_id;
    }

    row.innerHTML = `
      <th role="rowheader" headers="header-member" data-label="member email" id='row-header-${unique_id}'>
        ${member.member_display} ${admin_tagHTML} ${showMoreButton}
      </th>
      <td headers="header-last-active row-header-${unique_id}" data-sort-value="${last_active.sort_value}" data-label="last_active">
        ${last_active.display_value}
      </td>
      <td headers="header-action row-header-${unique_id}">
        <a href="${member.action_url}">
          <svg class="usa-icon" aria-hidden="true" focusable="false" role="img" width="24">
            <use xlink:href="/public/img/sprite.svg#${member.svg_icon}"></use>
          </svg>
          ${member.action_label} <span class="usa-sr-only">${member.name}</span>
        </a>
      </td>
      ${customTableOptions.hasAdditionalActions ? '<td>'+kebabHTML+'</td>' : ''}
    `;
    tbody.appendChild(row);
    if (domainsHTML || permissionsHTML) {
      tbody.appendChild(showMoreRow);
    }
    // This easter egg is only for fixtures that dont have names as we are displaying their emails
    // All prod users will have emails linked to their account
    if (customTableOptions.hasAdditionalActions) MembersTable.addMemberDeleteModal(num_domains, member.email || "Samwise Gamgee", member_delete_url, unique_id, row);
  }

  /**
   * Initializes "Show More" buttons on the page, enabling toggle functionality to show or hide content.
   * 
   * The function finds elements with "Show More" buttons and sets up a click event listener to toggle the visibility
   * of a corresponding content div. When clicked, the button updates its visual state (e.g., text/icon change),
   * and the associated content is shown or hidden based on its current visibility status.
   *
   * @function initShowMoreButtons
   */
  initShowMoreButtons() {
    /**
     * Toggles the visibility of a content section when the "Show More" button is clicked.
     * Updates the button text/icon based on whether the content is shown or hidden.
     *
     * @param {HTMLElement} toggleButton - The button that toggles the content visibility.
     * @param {HTMLElement} contentDiv - The content div whose visibility is toggled.
     * @param {HTMLElement} buttonParentRow - The parent row element containing the button.
     */
    function toggleShowMoreButton(toggleButton, contentDiv, buttonParentRow) {
      const spanElement = toggleButton.querySelector('span');
      const useElement = toggleButton.querySelector('use');
      if (contentDiv.classList.contains('display-none')) {
        showElement(contentDiv);
        spanElement.textContent = 'Close';
        useElement.setAttribute('xlink:href', '/public/img/sprite.svg#expand_less');
        buttonParentRow.classList.add('hide-td-borders');
        toggleButton.setAttribute('aria-label', 'Close additional information');
      } else {    
        hideElement(contentDiv);
        spanElement.textContent = 'Expand';
        useElement.setAttribute('xlink:href', '/public/img/sprite.svg#expand_more');
        buttonParentRow.classList.remove('hide-td-borders');
        toggleButton.setAttribute('aria-label', 'Expand for additional information');
      }
    }
  
    let toggleButtons = document.querySelectorAll('.usa-button--show-more-button');
    toggleButtons.forEach((toggleButton) => {
      
      // get contentDiv for element specified in data-for attribute of toggleButton
      let dataFor = toggleButton.dataset.for;
      let contentDiv = document.getElementById(dataFor);
      let buttonParentRow = toggleButton.parentElement.parentElement;
      if (contentDiv && contentDiv.tagName.toLowerCase() === 'tr' && contentDiv.classList.contains('show-more-content') && buttonParentRow && buttonParentRow.tagName.toLowerCase() === 'tr') {
        toggleButton.addEventListener('click', function() {
          toggleShowMoreButton(toggleButton, contentDiv, buttonParentRow);
        });
      } else {
        console.warn('Found a toggle button with no associated toggleable content or parent row');
      }

    });
  }

  /**
   * Converts a given `last_active` value into a display value and a numeric sort value.
   * The input can be a UTC date, the strings "Invited", "Invalid date", or null/undefined.
   * 
   * @param {string} last_active - UTC date string or special status like "Invited" or "Invalid date".
   * @returns {Object} - An object containing `display_value` (formatted date or status string) 
   *                     and `sort_value` (numeric value for sorting).
   */
  handleLastActive(last_active) {
    const invited = 'Invited';
    const invalid_date = 'Invalid date';
    const options = { year: 'numeric', month: 'long', day: 'numeric' }; // Date display format

    let display_value = invalid_date; // Default display value for invalid or null dates
    let sort_value = -1;              // Default sort value for invalid or null dates

    if (last_active === invited) {
      // Handle "Invited" status: special case with 0 sort value
      display_value = invited;
      sort_value = 0;
    } else if (last_active && last_active !== invalid_date) {
      // Parse and format valid UTC date strings
      const parsedDate = new Date(last_active);

      if (!isNaN(parsedDate.getTime())) {
        // Valid date
        display_value = parsedDate.toLocaleDateString('en-US', options);
        sort_value = parsedDate.getTime(); // Use timestamp for sorting
      } else {
        console.error(`Error: Invalid date string provided: ${last_active}`);
      }
    }

    return { display_value, sort_value };
  }

  /**
   * Generates HTML for the list of domains assigned to a member.
   * 
   * @param {number} num_domains - The number of domains the member is assigned to.
   * @param {Array} domain_names - An array of domain names.
   * @param {Array} domain_urls - An array of corresponding domain URLs.
   * @returns {string} - A string of HTML displaying the domains assigned to the member.
   */
  generateDomainsHTML(num_domains, domain_names, domain_urls, action_url) {
    // Initialize an empty string for the HTML
    let domainsHTML = '';

    // Only generate HTML if the member has one or more assigned domains
    if (num_domains > 0) {
      domainsHTML += "<div class='desktop:grid-col-5 margin-bottom-2 desktop:margin-bottom-0'>";
      domainsHTML += "<h4 class='margin-y-0'>Domains assigned</h4>";
      domainsHTML += `<p class='margin-y-0'>This member is assigned to ${num_domains} domains:</p>`;
      domainsHTML += "<ul class='usa-list usa-list--unstyled margin-y-0'>";

      // Display up to 6 domains with their URLs
      for (let i = 0; i < num_domains && i < 6; i++) {
        domainsHTML += `<li><a href="${domain_urls[i]}">${domain_names[i]}</a></li>`;
      }

      domainsHTML += "</ul>";

      // If there are more than 6 domains, display a "View assigned domains" link
      if (num_domains >= 6) {
        domainsHTML += `<p><a href="${action_url}/domains">View assigned domains</a></p>`;
      }

      domainsHTML += "</div>";
    }

    return domainsHTML;
  }

  /**
   * The POST call for deleting a Member and which error or success message it should return
   * and redirection if necessary
   * 
   * @param {string} member_delete_url - The URL for deletion ie `${member_type}-${member_id}/delete``
   * @param {*} pageToDisplay - If we're deleting the last item on a page that is not page 1, we'll need to display the previous page
   * Note: X-Request-With is used for security reasons to present CSRF attacks, the server checks that this header is present
   * (consent via CORS) so it knows it's not from a random request attempt
   */
  deleteMember(member_delete_url, pageToDisplay) {
    // Get CSRF token
    const csrfToken = getCsrfToken();
    // Create FormData object and append the CSRF token
    const formData = `csrfmiddlewaretoken=${encodeURIComponent(csrfToken)}`;

    fetch(`${member_delete_url}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrfToken,
      },
      body: formData
    })
    .then(response => {
      if (response.status === 200) {
        response.json().then(data => {
          if (data.success) {
            this.addAlert("success", data.success);
          }
          this.loadTable(pageToDisplay, this.currentSortBy, this.currentOrder, this.scrollToTable, this.currentStatus, this.currentSearchTerm);
        });
      } else {
        response.json().then(data => {
          if (data.error) {
            // This should display the error given from backend for
            // either only admin OR in progress requests
            this.addAlert("error", data.error); 
          } else {
            throw new Error(`Unexpected status: ${response.status}`);
          }
        });
      }
    })
    .catch(error => {
      console.error('Error deleting member:', error);
    });
  }


  /**
   * Adds an alert message to the page with an alert class.
   *
   * @param {string} alertClass - {error, warning, info, success}
   * @param {string} alertMessage - The text that will be displayed
   *
   */
  addAlert(alertClass, alertMessage) {
    let toggleableAlertDiv = document.getElementById("toggleable-alert");
    this.resetAlerts();
    toggleableAlertDiv.classList.add(`usa-alert--${alertClass}`);
    let alertParagraph = toggleableAlertDiv.querySelector(".usa-alert__text");
    alertParagraph.innerHTML = alertMessage
    showElement(toggleableAlertDiv);
  }

  /**
   * Resets the reusable alert message
   */
  resetAlerts() {
    // Create a list of any alert that's leftover and remove
    document.querySelectorAll(".usa-alert:not(#toggleable-alert)").forEach(alert => {
      alert.remove();
    });
    let toggleableAlertDiv = document.getElementById("toggleable-alert");
    toggleableAlertDiv.classList.remove('usa-alert--error');
    toggleableAlertDiv.classList.remove('usa-alert--success');
    hideElement(toggleableAlertDiv);
  }

  /**
   * Generates an HTML string summarizing a user's additional permissions within a portfolio, 
   * based on the user's permissions and predefined permission choices.
   *
   * @param {Array} member_permissions - An array of permission strings that the member has.
   * @param {Object} UserPortfolioPermissionChoices - An object containing predefined permission choice constants.
   *        Expected keys include:
   *        - VIEW_ALL_DOMAINS
   *        - VIEW_MANAGED_DOMAINS
   *        - EDIT_REQUESTS
   *        - VIEW_ALL_REQUESTS
   *        - EDIT_MEMBERS
   *        - VIEW_MEMBERS
   * 
   * @returns {string} - A string of HTML representing the user's additional permissions.
   *                     If the user has no specific permissions, it returns a default message
   *                     indicating no additional permissions.
   *
   * Behavior:
   * - The function checks the user's permissions (`member_permissions`) and generates
   *   corresponding HTML sections based on the permission choices defined in `UserPortfolioPermissionChoices`.
   * - Permissions are categorized into domains, requests, and members:
   *   - Domains: Determines whether the user can view or manage all or assigned domains.
   *   - Requests: Differentiates between users who can edit requests, view all requests, or have no request privileges.
   *   - Members: Distinguishes between members who can manage or only view other members.
   * - If no relevant permissions are found, the function returns a message stating that the user has no additional permissions.
   * - The resulting HTML always includes a header "Additional permissions for this member" and appends the relevant permission descriptions.
   */
  generatePermissionsHTML(member_permissions, UserPortfolioPermissionChoices) {
    let permissionsHTML = '';

    // Check domain-related permissions
    if (member_permissions.includes(UserPortfolioPermissionChoices.VIEW_ALL_DOMAINS)) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><strong class='text-base-dark'>Domains:</strong> Can view all organization domains. Can manage domains they are assigned to and edit information about the domain (including DNS settings).</p>";
    } else if (member_permissions.includes(UserPortfolioPermissionChoices.VIEW_MANAGED_DOMAINS)) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><strong class='text-base-dark'>Domains:</strong> Can manage domains they are assigned to and edit information about the domain (including DNS settings).</p>";
    }

    // Check request-related permissions
    if (member_permissions.includes(UserPortfolioPermissionChoices.EDIT_REQUESTS)) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><strong class='text-base-dark'>Domain requests:</strong> Can view all organization domain requests. Can create domain requests and modify their own requests.</p>";
    } else if (member_permissions.includes(UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS)) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><strong class='text-base-dark'>Domain requests (view-only):</strong> Can view all organization domain requests. Can't create or modify any domain requests.</p>";
    }

    // Check member-related permissions
    if (member_permissions.includes(UserPortfolioPermissionChoices.EDIT_MEMBERS)) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><strong class='text-base-dark'>Members:</strong> Can manage members including inviting new members, removing current members, and assigning domains to members.</p>";
    } else if (member_permissions.includes(UserPortfolioPermissionChoices.VIEW_MEMBERS)) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><strong class='text-base-dark'>Members (view-only):</strong> Can view all organizational members. Can't manage any members.</p>";
    }

    // If no specific permissions are assigned, display a message indicating no additional permissions
    if (!permissionsHTML) {
      permissionsHTML += "<p class='margin-top-1 p--blockquote'><b>No additional permissions:</b> There are no additional permissions for this member.</p>";
    }

    // Add a permissions header and wrap the entire output in a container
    permissionsHTML = "<div class='desktop:grid-col-7'><h4 class='margin-y-0'>Additional permissions for this member</h4>" + permissionsHTML + "</div>";
    
    return permissionsHTML;
  }

  /**
   * Modal that displays when deleting a domain request 
   * @param {string} num_domains - Number of domain a user has within the org
   * @param {string} member_email - The member's email
   * @param {string} submit_delete_url - `${member_type}-${member_id}/delete`
   * @param {HTMLElement} wrapper_element - The element to which the modal is appended
   */
  static addMemberDeleteModal(num_domains, member_email, submit_delete_url, id, wrapper_element) {

    let modalHeading = ``;
    let modalDescription = ``;

    if (num_domains >= 0){
      modalHeading = `Are you sure you want to delete ${member_email}?`;
      modalDescription = `They will no longer be able to access this organization. 
      This action cannot be undone.`;
      if (num_domains >= 1)
      {
        modalDescription = `<b>${member_email}</b> currently manages ${num_domains} domain${num_domains > 1 ? "s": ""} in the organization.
        Removing them from the organization will remove them from all of their domains. They will no longer be able to
        access this organization. This action cannot be undone.`;
      }
    }

    const modalSubmit = `
      <button type="button"
      class="usa-button usa-button--secondary usa-modal__submit"
      data-pk = ${submit_delete_url}
      name="delete-member">Yes, remove from organization</button>
    `

    addModal(`toggle-remove-member-${id}`, 'Are you sure you want to continue?', 'Member will be removed', modalHeading, modalDescription, modalSubmit, wrapper_element, true);
  }
}

export function initMembersTable() {
  document.addEventListener('DOMContentLoaded', function() {
    const isMembersPage = document.getElementById("members") 
    if (isMembersPage){
      const membersTable = new MembersTable();
      if (membersTable.tableWrapper) {
        // Initial load
        membersTable.loadTable(1);
      }
    }
  });
}
