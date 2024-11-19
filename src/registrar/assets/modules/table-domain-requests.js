import { hideElement, showElement, scrollToElement } from './helpers.js';
import { initializeModals, unloadModals } from './helpers-uswds.js';
import { getCsrfToken } from './helpers-csrf-token.js';

import { LoadTableBase } from './table-base.js';


const utcDateString = (dateString) => {
  const date = new Date(dateString);
  const utcYear = date.getUTCFullYear();
  const utcMonth = date.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
  const utcDay = date.getUTCDate().toString().padStart(2, '0');
  let utcHours = date.getUTCHours();
  const utcMinutes = date.getUTCMinutes().toString().padStart(2, '0');

  const ampm = utcHours >= 12 ? 'PM' : 'AM';
  utcHours = utcHours % 12 || 12;  // Convert to 12-hour format, '0' hours should be '12'

  return `${utcMonth} ${utcDay}, ${utcYear}, ${utcHours}:${utcMinutes} ${ampm} UTC`;
};


export class DomainRequestsTable extends LoadTableBase {

  constructor() {
    super('domain-requests');
  }
  
  toggleExportButton(requests) {
    const exportButton = document.getElementById('export-csv'); 
    if (exportButton) {
        if (requests.length > 0) {
            showElement(exportButton);
        } else {
            hideElement(exportButton);
        }
    }
}

  /**
     * Loads rows in the domains list, as well as updates pagination around the domains list
     * based on the supplied attributes.
     * @param {*} page - the page number of the results (starts with 1)
     * @param {*} sortBy - the sort column option
     * @param {*} order - the sort order {asc, desc}
     * @param {*} scroll - control for the scrollToElement functionality
     * @param {*} status - control for the status filter
     * @param {*} searchTerm - the search term
     * @param {*} portfolio - the portfolio id
     */
  loadTable(page, sortBy = this.currentSortBy, order = this.currentOrder, scroll = this.scrollToTable, status = this.currentStatus, searchTerm = this.currentSearchTerm, portfolio = this.portfolioValue) {
    let baseUrl = document.getElementById("get_domain_requests_json_url");
    
    if (!baseUrl) {
      return;
    }

    let baseUrlValue = baseUrl.innerHTML;
    if (!baseUrlValue) {
      return;
    }

    // add searchParams
    let searchParams = new URLSearchParams(
      {
        "page": page,
        "sort_by": sortBy,
        "order": order,
        "status": status,
        "search_term": searchTerm
      }
    );
    if (portfolio)
      searchParams.append("portfolio", portfolio)

    let url = `${baseUrlValue}?${searchParams.toString()}`
    fetch(url)
      .then(response => response.json())
      .then(data => {
        if (data.error) {
          console.error('Error in AJAX call: ' + data.error);
          return;
        }

        // Manage "export as CSV" visibility for domain requests
        this.toggleExportButton(data.domain_requests);

        // handle the display of proper messaging in the event that no requests exist in the list or search returns no results
        this.updateDisplay(data, this.tableWrapper, this.noTableWrapper, this.noSearchResultsWrapper, this.currentSearchTerm);

        // identify the DOM element where the domain request list will be inserted into the DOM
        const tbody = document.querySelector('#domain-requests tbody');
        tbody.innerHTML = '';

        // Unload modals will re-inject the DOM with the initial placeholders to allow for .on() in regular use cases
        // We do NOT want that as it will cause multiple placeholders and therefore multiple inits on delete,
        // which will cause bad delete requests to be sent.
        const preExistingModalPlaceholders = document.querySelectorAll('[data-placeholder-for^="toggle-delete-domain-alert"]');
        preExistingModalPlaceholders.forEach(element => {
            element.remove();
        });

        // remove any existing modal elements from the DOM so they can be properly re-initialized
        // after the DOM content changes and there are new delete modal buttons added
        unloadModals();

        let needsDeleteColumn = false;

        needsDeleteColumn = data.domain_requests.some(request => request.is_deletable);

        // Remove existing delete th and td if they exist
        let existingDeleteTh =  document.querySelector('.delete-header');
        if (!needsDeleteColumn) {
          if (existingDeleteTh)
            existingDeleteTh.remove();
        } else {
          if (!existingDeleteTh) {
            const delheader = document.createElement('th');
            delheader.setAttribute('scope', 'col');
            delheader.setAttribute('role', 'columnheader');
            delheader.setAttribute('class', 'delete-header');
            delheader.innerHTML = `
              <span class="usa-sr-only">Delete Action</span>`;
            let tableHeaderRow = document.querySelector('#domain-requests thead tr');
            tableHeaderRow.appendChild(delheader);
          }
        }

        data.domain_requests.forEach(request => {
          const options = { year: 'numeric', month: 'short', day: 'numeric' };
          const domainName = request.requested_domain ? request.requested_domain : `New domain request <br><span class="text-base font-body-xs">(${utcDateString(request.created_at)})</span>`;
          const actionUrl = request.action_url;
          const actionLabel = request.action_label;
          const submissionDate = request.last_submitted_date ? new Date(request.last_submitted_date).toLocaleDateString('en-US', options) : `<span class="text-base">Not submitted</span>`;
          
          // The markup for the delete function either be a simple trigger or a 3 dots menu with a hidden trigger (in the case of portfolio requests page)
          // If the request is not deletable, use the following (hidden) span for ANDI screenreaders to indicate this state to the end user
          let modalTrigger =  `
          <span class="usa-sr-only">Domain request cannot be deleted now. Edit the request for more information.</span>`;

          let markupCreatorRow = '';

          if (this.portfolioValue) {
            markupCreatorRow = `
              <td>
                  <span class="text-wrap break-word">${request.creator ? request.creator : ''}</span>
              </td>
            `
          }

          if (request.is_deletable) {
            // If the request is deletable, create modal body and insert it. This is true for both requests and portfolio requests pages
            let modalHeading = '';
            let modalDescription = '';

            if (request.requested_domain) {
              modalHeading = `Are you sure you want to delete ${request.requested_domain}?`;
              modalDescription = 'This will remove the domain request from the .gov registrar. This action cannot be undone.';
            } else {
              if (request.created_at) {
                modalHeading = 'Are you sure you want to delete this domain request?';
                modalDescription = `This will remove the domain request (created ${utcDateString(request.created_at)}) from the .gov registrar. This action cannot be undone`;
              } else {
                modalHeading = 'Are you sure you want to delete New domain request?';
                modalDescription = 'This will remove the domain request from the .gov registrar. This action cannot be undone.';
              }
            }

            modalTrigger = `
              <a 
                role="button" 
                id="button-toggle-delete-domain-alert-${request.id}"
                href="#toggle-delete-domain-alert-${request.id}"
                class="usa-button text-secondary usa-button--unstyled text-no-underline late-loading-modal-trigger line-height-sans-5"
                aria-controls="toggle-delete-domain-alert-${request.id}"
                data-open-modal
              >
                <svg class="usa-icon" aria-hidden="true" focusable="false" role="img" width="24">
                  <use xlink:href="/public/img/sprite.svg#delete"></use>
                </svg> Delete <span class="usa-sr-only">${domainName}</span>
              </a>`

            const modalSubmit = `
              <button type="button"
              class="usa-button usa-button--secondary usa-modal__submit"
              data-pk = ${request.id}
              name="delete-domain-request">Yes, delete request</button>
            `

            const modal = document.createElement('div');
            modal.setAttribute('class', 'usa-modal');
            modal.setAttribute('id', `toggle-delete-domain-alert-${request.id}`);
            modal.setAttribute('aria-labelledby', 'Are you sure you want to continue?');
            modal.setAttribute('aria-describedby', 'Domain will be removed');
            modal.setAttribute('data-force-action', '');

            modal.innerHTML = `
              <div class="usa-modal__content">
                <div class="usa-modal__main">
                  <h2 class="usa-modal__heading" id="modal-1-heading">
                    ${modalHeading}
                  </h2>
                  <div class="usa-prose">
                    <p id="modal-1-description">
                      ${modalDescription}
                    </p>
                  </div>
                  <div class="usa-modal__footer">
                      <ul class="usa-button-group">
                        <li class="usa-button-group__item">
                          ${modalSubmit}
                        </li>      
                        <li class="usa-button-group__item">
                            <button
                                type="button"
                                class="usa-button usa-button--unstyled padding-105 text-center"
                                data-close-modal
                            >
                                Cancel
                            </button>
                        </li>
                      </ul>
                  </div>
                </div>
                <button
                  type="button"
                  class="usa-button usa-modal__close"
                  aria-label="Close this window"
                  data-close-modal
                >
                  <svg class="usa-icon" aria-hidden="true" focusable="false" role="img">
                    <use xlink:href="/public/img/sprite.svg#close"></use>
                  </svg>
                </button>
              </div>
              `

            this.tableWrapper.appendChild(modal);

            // Request is deletable, modal and modalTrigger are built. Now check if we are on the portfolio requests page (by seeing if there is a portfolio value) and enhance the modalTrigger accordingly
            if (this.portfolioValue) {
              modalTrigger = `
              <a 
                role="button" 
                id="button-toggle-delete-domain-alert-${request.id}"
                href="#toggle-delete-domain-alert-${request.id}"
                class="usa-button text-secondary usa-button--unstyled text-no-underline late-loading-modal-trigger margin-top-2 visible-mobile-flex line-height-sans-5"
                aria-controls="toggle-delete-domain-alert-${request.id}"
                data-open-modal
              >
                <svg class="usa-icon" aria-hidden="true" focusable="false" role="img" width="24">
                  <use xlink:href="/public/img/sprite.svg#delete"></use>
                </svg> Delete <span class="usa-sr-only">${domainName}</span>
              </a>

              <div class="usa-accordion usa-accordion--more-actions margin-right-2 hidden-mobile-flex">
                <div class="usa-accordion__heading">
                  <button
                    type="button"
                    class="usa-button usa-button--unstyled usa-button--with-icon usa-accordion__button usa-button--more-actions"
                    aria-expanded="false"
                    aria-controls="more-actions-${request.id}"
                  >
                    <svg class="usa-icon top-2px" aria-hidden="true" focusable="false" role="img" width="24">
                      <use xlink:href="/public/img/sprite.svg#more_vert"></use>
                    </svg>
                  </button>
                </div>
                <div id="more-actions-${request.id}" class="usa-accordion__content usa-prose shadow-1 left-auto right-0" hidden>
                  <h2>More options</h2>
                  <a 
                    role="button" 
                    id="button-toggle-delete-domain-alert-${request.id}"
                    href="#toggle-delete-domain-alert-${request.id}"
                    class="usa-button text-secondary usa-button--unstyled text-no-underline late-loading-modal-trigger margin-top-2 line-height-sans-5"
                    aria-controls="toggle-delete-domain-alert-${request.id}"
                    data-open-modal
                  >
                    <svg class="usa-icon" aria-hidden="true" focusable="false" role="img" width="24">
                      <use xlink:href="/public/img/sprite.svg#delete"></use>
                    </svg> Delete <span class="usa-sr-only">${domainName}</span>
                  </a>
                </div>
              </div>
              `
            }
          }


          const row = document.createElement('tr');
          row.innerHTML = `
            <th scope="row" role="rowheader" data-label="Domain name">
              ${domainName}
            </th>
            <td data-sort-value="${new Date(request.last_submitted_date).getTime()}" data-label="Date submitted">
              ${submissionDate}
            </td>
            ${markupCreatorRow}
            <td data-label="Status">
              ${request.status}
            </td>
            <td>
              <a href="${actionUrl}">
                <svg class="usa-icon" aria-hidden="true" focusable="false" role="img" width="24">
                  <use xlink:href="/public/img/sprite.svg#${request.svg_icon}"></use>
                </svg>
                ${actionLabel} <span class="usa-sr-only">${request.requested_domain ? request.requested_domain : 'New domain request'}</span>
              </a>
            </td>
            ${needsDeleteColumn ? '<td>'+modalTrigger+'</td>' : ''}
          `;
          tbody.appendChild(row);
        });

        // initialize modals immediately after the DOM content is updated
        initializeModals();

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
            let pageToDisplay = data.page;
            if (data.total == 1 && data.unfiltered_total > 1) {
              pageToDisplay--;
            }
            this.deleteDomainRequest(pk, pageToDisplay);
          });
        });

        // Do not scroll on first page load
        if (scroll)
          scrollToElement('class', 'domain-requests');
        this.scrollToTable = true;

        // update the pagination after the domain requests list is updated
        this.updatePagination(
          'domain request',
          '#domain-requests-pagination',
          '#domain-requests-pagination .usa-pagination__counter',
          '#domain-requests',
          data.page,
          data.num_pages,
          data.has_previous,
          data.has_next,
          data.total,
        );
        this.currentSortBy = sortBy;
        this.currentOrder = order;
        this.currentSearchTerm = searchTerm;
      })
      .catch(error => console.error('Error fetching domain requests:', error));
  }

  /**
   * Delete is actually a POST API that requires a csrf token. The token will be waiting for us in the template as a hidden input.
   * @param {*} domainRequestPk - the identifier for the request that we're deleting
   * @param {*} pageToDisplay - If we're deleting the last item on a page that is not page 1, we'll need to display the previous page
  */
  deleteDomainRequest(domainRequestPk, pageToDisplay) {
    // Use to debug uswds modal issues
    //console.log('deleteDomainRequest')
    
    // Get csrf token
    const csrfToken = getCsrfToken();
    // Create FormData object and append the CSRF token
    const formData = `csrfmiddlewaretoken=${encodeURIComponent(csrfToken)}&delete-domain-request=`;

    fetch(`/domain-request/${domainRequestPk}/delete`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': csrfToken,
      },
      body: formData
    })
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      // Update data and UI
      this.loadTable(pageToDisplay, this.currentSortBy, this.currentOrder, this.scrollToTable, this.currentSearchTerm);
    })
    .catch(error => console.error('Error fetching domain requests:', error));
  }
}

export function initDomainRequestsTable() { 
  document.addEventListener('DOMContentLoaded', function() {
    const domainRequestsSectionWrapper = document.getElementById('domain-requests');
    if (domainRequestsSectionWrapper) {
      const domainRequestsTable = new DomainRequestsTable();
      if (domainRequestsTable.tableWrapper) {
        domainRequestsTable.loadTable(1);
      }
    }

    document.addEventListener('focusin', function(event) {
      closeOpenAccordions(event);
    });
    
    document.addEventListener('click', function(event) {
      closeOpenAccordions(event);
    });

    function closeMoreActionMenu(accordionThatIsOpen) {
      if (accordionThatIsOpen.getAttribute("aria-expanded") === "true") {
        accordionThatIsOpen.click();
      }
    }

    function closeOpenAccordions(event) {
      const openAccordions = document.querySelectorAll('.usa-button--more-actions[aria-expanded="true"]');
      openAccordions.forEach((openAccordionButton) => {
        // Find the corresponding accordion
        const accordion = openAccordionButton.closest('.usa-accordion--more-actions');
        if (accordion && !accordion.contains(event.target)) {
          // Close the accordion if the click is outside
          closeMoreActionMenu(openAccordionButton);
        }
      });
    }
  });
}