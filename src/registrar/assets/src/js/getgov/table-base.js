import { hideElement, showElement, toggleCaret, scrollToElement } from './helpers.js';

/**
* Creates and adds a modal dialog to the DOM with customizable attributes and content.
*
* @param {string} id - A unique identifier for the modal, appended to the action for uniqueness.
* @param {string} ariaLabelledby - The ID of the element that labels the modal, for accessibility.
* @param {string} ariaDescribedby - The ID of the element that describes the modal, for accessibility.
* @param {string} modalHeading - The heading text displayed at the top of the modal.
* @param {string} modalDescription - The main descriptive text displayed within the modal.
* @param {string} modalSubmit - The HTML content for the submit button, allowing customization.
* @param {HTMLElement} wrapper_element - Optional. The element to which the modal is appended. If not provided, defaults to `document.body`.
* @param {boolean} forceAction - Optional. If true, adds a `data-force-action` attribute to the modal for additional control.
*
* The modal includes a heading, description, submit button, and a cancel button, along with a close button.
* The `data-close-modal` attribute is added to cancel and close buttons to enable closing functionality.
*/
export function addModal(id, ariaLabelledby, ariaDescribedby, modalHeading, modalDescription, modalSubmit, wrapper_element, forceAction) {

 const modal = document.createElement('div');
 modal.setAttribute('class', 'usa-modal');
 modal.setAttribute('id', id);
 modal.setAttribute('aria-labelledby', ariaLabelledby);
 modal.setAttribute('aria-describedby', ariaDescribedby);
 if (forceAction)
   modal.setAttribute('data-force-action', ''); 

 modal.innerHTML = `
   <div class="usa-modal__content">
     <div class="usa-modal__main">
       <h2 class="usa-modal__heading">
         ${modalHeading}
       </h2>
       <div class="usa-prose">
         <p>
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
 if (wrapper_element) {
   wrapper_element.appendChild(modal);
 } else {
   document.body.appendChild(modal);
 }
}

/**
 * Helper function that creates a dynamic accordion navigation
 * @param {string} action - The action type or identifier used to create a unique DOM IDs.
 * @param {string} unique_id - An ID that when combined with action makes a unique identifier
 * @param {string} modal_button_text - The action button's text
 * @param {string} screen_reader_text - A screen reader helper
 */
export function generateKebabHTML(action, unique_id, modal_button_text, screen_reader_text) {
  const generateModalButton = (mobileOnly = false) => `
    <a 
      role="button" 
      id="button-trigger-${action}-${unique_id}"
      href="#toggle-${action}-${unique_id}"
      class="usa-button usa-button--unstyled text-no-underline late-loading-modal-trigger margin-top-2 line-height-sans-5 text-secondary ${mobileOnly ? 'visible-mobile-flex' : ''}"
      aria-controls="toggle-${action}-${unique_id}"
      data-open-modal
    >
      ${mobileOnly ? `<svg class="usa-icon" aria-hidden="true" focusable="false" role="img" width="24">
        <use xlink:href="/public/img/sprite.svg#delete"></use>
      </svg>` : ''}
      ${modal_button_text}
    </a>
  `;

  // Main kebab structure
  const kebab = `
    ${generateModalButton(true)} <!-- Mobile button -->
    <div class="usa-accordion usa-accordion--more-actions margin-right-2 hidden-mobile-flex">
      <div class="usa-accordion__heading">
        <button
          type="button"
          class="usa-button usa-button--unstyled usa-button--with-icon usa-accordion__button usa-button--more-actions"
          aria-expanded="false"
          aria-controls="more-actions-${unique_id}"
          aria-label="${screen_reader_text}"
        >
          <svg class="usa-icon top-2px" aria-hidden="true" focusable="false" role="img" width="24">
            <use xlink:href="/public/img/sprite.svg#more_vert"></use>
          </svg>
        </button>
      </div>
      <div id="more-actions-${unique_id}" class="usa-accordion__content usa-prose shadow-1 left-auto right-neg-1" hidden>
        <h2>More options</h2>
        ${generateModalButton()} <!-- Desktop button -->
      </div>
    </div>
  `;

  return kebab;
}

export class BaseTable {
  constructor(itemName) {
    this.itemName = itemName;
    this.displayName = itemName;
    this.sectionSelector = itemName + 's';
    this.tableWrapper = document.getElementById(`${this.sectionSelector}__table-wrapper`);
    this.tableHeaders = document.querySelectorAll(`#${this.sectionSelector} th[data-sortable]`);
    this.currentSortBy = 'id';
    this.currentOrder = 'asc';
    this.currentStatus = [];
    this.currentSearchTerm = '';
    this.scrollToTable = false;
    this.searchInput = document.getElementById(`${this.sectionSelector}__search-field`);
    this.searchSubmit = document.getElementById(`${this.sectionSelector}__search-field-submit`);
    this.tableAnnouncementRegion = document.getElementById(`${this.sectionSelector}__usa-table__announcement-region`);
    this.resetSearchButton = document.getElementById(`${this.sectionSelector}__reset-search`);
    this.resetFiltersButton = document.getElementById(`${this.sectionSelector}__reset-filters`);
    this.statusCheckboxes = document.querySelectorAll(`.${this.sectionSelector} input[name="filter-status"]`);
    this.statusIndicator = document.getElementById(`${this.sectionSelector}__filter-indicator`);
    this.statusToggle = document.getElementById(`${this.sectionSelector}__usa-button--filter`);
    this.noDataTableWrapper = document.getElementById(`${this.sectionSelector}__no-data`);
    this.noSearchResultsWrapper = document.getElementById(`${this.sectionSelector}__no-search-results`);
    this.portfolioElement = document.getElementById('portfolio-js-value');
    this.portfolioValue = this.portfolioElement ? this.portfolioElement.getAttribute('data-portfolio') : null;
    this.initializeTableHeaders();
    this.initializeSearchHandler();
    this.initializeStatusToggleHandler();
    this.initializeFilterCheckboxes();
    this.initializeResetSearchButton();
    this.initializeResetFiltersButton();
    this.initializeAccordionAccessibilityListeners();
  }

  /**
    * Generalized function to update pagination for a list.
   * @param {number} currentPage - The current page number (starting with 1).
   * @param {number} numPages - The total number of pages.
   * @param {boolean} hasPrevious - Whether there is a page before the current page.
   * @param {boolean} hasNext - Whether there is a page after the current page.
   * @param {number} total - The total number of items.
  */  
  updatePagination(
    currentPage,
    numPages,
    hasPrevious,
    hasNext,
    totalItems
  ) {
    const paginationButtons = document.querySelector(`#${this.sectionSelector}-pagination .usa-pagination__list`);
    const counterSelectorEl = document.querySelector(`#${this.sectionSelector}-pagination .usa-pagination__counter`);
    const paginationSelectorEl = document.querySelector(`#${this.sectionSelector}-pagination`);
    const parentTableSelector = `#${this.sectionSelector}`;
    counterSelectorEl.innerHTML = '';
    paginationButtons.innerHTML = '';

    // Buttons should only be displayed if there are more than one pages of results
    paginationButtons.classList.toggle('display-none', numPages <= 1);

    // Counter should only be displayed if there is more than 1 item
    paginationSelectorEl.classList.toggle('display-none', totalItems < 1);

    counterSelectorEl.innerHTML = `${totalItems} ${this.displayName}${totalItems > 1 ? 's' : ''}${this.currentSearchTerm ? ' for ' + '"' + this.currentSearchTerm + '"' : ''}`;

    // Helper function to create a pagination item
    const createPaginationItem = (page) => {
      const paginationItem = document.createElement('li');
      paginationItem.classList.add('usa-pagination__item', 'usa-pagination__page-no');
      paginationItem.innerHTML = `
        <a href="${parentTableSelector}" class="usa-pagination__button" aria-label="Page ${page}">${page}</a>
      `;
      if (page === currentPage) {
        paginationItem.querySelector('a').classList.add('usa-current');
        paginationItem.querySelector('a').setAttribute('aria-current', 'page');
      }
      paginationItem.querySelector('a').addEventListener('click', (event) => {
        event.preventDefault();
        this.loadTable(page);
      });
      return paginationItem;
    };

    if (hasPrevious) {
      const prevPaginationItem = document.createElement('li');
      prevPaginationItem.className = 'usa-pagination__item usa-pagination__arrow';
      prevPaginationItem.innerHTML = `
        <a href="${parentTableSelector}" class="usa-pagination__link usa-pagination__previous-page" aria-label="Previous page">
          <svg class="usa-icon" aria-hidden="true" role="img">
            <use xlink:href="/public/img/sprite.svg#navigate_before"></use>
          </svg>
          <span class="usa-pagination__link-text">Previous</span>
        </a>
      `;
      prevPaginationItem.querySelector('a').addEventListener('click', (event) => {
        event.preventDefault();
        this.loadTable(currentPage - 1);
      });
      paginationButtons.appendChild(prevPaginationItem);
    }

    // Add first page and ellipsis if necessary
    if (currentPage > 2) {
      paginationButtons.appendChild(createPaginationItem(1));
      if (currentPage > 3) {
        const ellipsis = document.createElement('li');
        ellipsis.className = 'usa-pagination__item usa-pagination__overflow';
        ellipsis.setAttribute('aria-label', 'ellipsis indicating non-visible pages');
        ellipsis.innerHTML = '<span>…</span>';
        paginationButtons.appendChild(ellipsis);
      }
    }

    // Add pages around the current page
    for (let i = Math.max(1, currentPage - 1); i <= Math.min(numPages, currentPage + 1); i++) {
      paginationButtons.appendChild(createPaginationItem(i));
    }

    // Add last page and ellipsis if necessary
    if (currentPage < numPages - 1) {
      if (currentPage < numPages - 2) {
        const ellipsis = document.createElement('li');
        ellipsis.className = 'usa-pagination__item usa-pagination__overflow';
        ellipsis.setAttribute('aria-label', 'ellipsis indicating non-visible pages');
        ellipsis.innerHTML = '<span>…</span>';
        paginationButtons.appendChild(ellipsis);
      }
      paginationButtons.appendChild(createPaginationItem(numPages));
    }

    if (hasNext) {
      const nextPaginationItem = document.createElement('li');
      nextPaginationItem.className = 'usa-pagination__item usa-pagination__arrow';
      nextPaginationItem.innerHTML = `
        <a href="${parentTableSelector}" class="usa-pagination__link usa-pagination__next-page" aria-label="Next page">
          <span class="usa-pagination__link-text">Next</span>
          <svg class="usa-icon" aria-hidden="true" role="img">
            <use xlink:href="/public/img/sprite.svg#navigate_next"></use>
          </svg>
        </a>
      `;
      nextPaginationItem.querySelector('a').addEventListener('click', (event) => {
        event.preventDefault();
        this.loadTable(currentPage + 1);
      });
      paginationButtons.appendChild(nextPaginationItem);
    }
  }

  /**
   * A helper that toggles content/ no content/ no search results based on results in data.
   * @param {Object} data - Data representing current page of results data.
   * @param {HTMLElement} dataWrapper - The DOM element to show if there are results on the current page.
   * @param {HTMLElement} noDataWrapper - The DOM element to show if there are no results period.
   * @param {HTMLElement} noSearchResultsWrapper - The DOM element to show if there are no results in the current filtered search.
  */
  updateDisplay = (data, dataWrapper, noDataWrapper, noSearchResultsWrapper) => {
    const { unfiltered_total, total } = data;
    if (unfiltered_total) {
      if (total) {
        showElement(dataWrapper);
        hideElement(noSearchResultsWrapper);
        hideElement(noDataWrapper);
      } else {
        hideElement(dataWrapper);
        showElement(noSearchResultsWrapper);
        hideElement(noDataWrapper);
      }
    } else {
      hideElement(dataWrapper);
      hideElement(noSearchResultsWrapper);
      showElement(noDataWrapper);
    }
  };

  /**
   * A helper that resets sortable table headers
   *
  */
  unsetHeader = (header) => {
    header.removeAttribute('aria-sort');
    let headerName = header.innerText;
    const headerLabel = `${headerName}, sortable column, currently unsorted"`;
    const headerButtonLabel = `Click to sort by ascending order.`;
    header.setAttribute("aria-label", headerLabel);
    header.querySelector('.usa-table__header__button').setAttribute("title", headerButtonLabel);
  };

  /**
   * Generates search params for filtering and sorting
   * @param {number} page - The current page number for pagination (starting with 1)
   * @param {*} sortBy - The sort column option
   * @param {*} order - The order of sorting {asc, desc}
   * @param {string} searchTerm - The search term used to filter results for a specific keyword
   * @param {*} status - The status filter applied {ready, dns_needed, etc}
   * @param {string} portfolio - The portfolio id
   */
  getSearchParams(page, sortBy, order, searchTerm, status, portfolio) {
    let searchParams = new URLSearchParams(
      {
        "page": page,
        "sort_by": sortBy,
        "order": order,
        "search_term": searchTerm,
      }
    );

    let emailValue = this.portfolioElement ? this.portfolioElement.getAttribute('data-email') : null;
    let memberIdValue = this.portfolioElement ? this.portfolioElement.getAttribute('data-member-id') : null;
    let memberOnly = this.portfolioElement ? this.portfolioElement.getAttribute('data-member-only') : null;

    if (portfolio)
      searchParams.append("portfolio", portfolio);
    if (emailValue)
      searchParams.append("email", emailValue);
    if (memberIdValue)
      searchParams.append("member_id", memberIdValue);
    if (memberOnly)
      searchParams.append("member_only", memberOnly);
    if (status)
      searchParams.append("status", status);
    return searchParams;
  }

  /**
   * Gets the base URL of API requests
   * Placeholder function in a parent class - method should be implemented by child class for specifics
   * Throws an error if called directly from the parent class
   */
  getBaseUrl() {
    throw new Error('getBaseUrl must be defined');
  }

  /**
   * Calls "uswdsUnloadModals" to remove any existing modal element to make sure theres no unintended consequences 
   * from leftover event listeners + can be properly re-initialized
   */
  unloadModals(){}

  /**
   * Loads modals + sets up event listeners for the modal submit actions
   * "Activates" the modals after the DOM updates 
   * Utilizes "uswdsInitializeModals"
   * Adds click event listeners to each modal's submit button so we can handle a user's actions
   *
   * When the submit button is clicked:
   * - Triggers the close button to reset modal classes
   * - Determines if the page needs refreshing if the last item is deleted
   * @param {number} page - The current page number for pagination
   * @param {number} total - The total # of items on the current page
   * @param {number} unfiltered_total - The total # of items across all pages
   */
  loadModals(page, total, unfiltered_total) {}

  /**
   * Loads tooltips + sets up event listeners
   * "Activates" the tooltips after the DOM updates 
   * Utilizes "uswdsInitializeTooltips"
  */
  initializeTooltips() {}

  /**
   * Allows us to customize the table display based on specific conditions and a user's permissions
   * Dynamically manages the visibility set up of columns, adding/removing headers 
   * (ie if a domain request is deleteable, we include the kebab column or if a user has edit permissions
   * for a member, they will also see the kebab column)
   * @param {Object} dataObjects - Data which contains info on domain requests or a user's permission
   * Currently returns a dictionary of either:
   * - "hasAdditionalActions": If additional elements need to be added to the Action column 
   * - "UserPortfolioPermissionChoices": A user's portfolio permission choices 
   */
  customizeTable(dataObjects){ return {}; }

  /**
   * Retrieves specific data objects
   * Placeholder function in a parent class - method should be implemented by child class for specifics
   * Throws an error if called directly from the parent class
   * Returns either: data.members, data.domains or data.domain_requests
   * @param {Object} data - The full data set from which a subset of objects is extracted.
   */
  getDataObjects(data) {
    throw new Error('getDataObjects must be defined');
  }

  /**
   * Creates + appends a row to a tbody element
   * Tailored structure set up for each data object (domain, domain_request, member, etc) 
   * Placeholder function in a parent class - method should be implemented by child class for specifics
   * Throws an error if called directly from the parent class
   * Returns either: data.members, data.domains or data.domain_requests
   * @param {Object} dataObject - The data used to populate the row content 
   * @param {HTMLElement} tbody - The table body to which the new row is appended to 
   * @param {Object} customTableOptions - Additional options for customizing row appearance (ie hasAdditionalActions)
   */
  addRow(dataObject, tbody, customTableOptions) {
    throw new Error('addRow must be defined');
  }

  /**
   * See function for more details
   */
  initShowMoreButtons(){}

  /**
   * See function for more details
   */
  initCheckboxListeners(){}

  /**
   * Loads rows in the members list, as well as updates pagination around the members list
   * based on the supplied attributes.
   * @param {*} page - The page number of the results (starts with 1)
   * @param {*} sortBy - The sort column option
   * @param {*} order - The sort order {asc, desc}
   * @param {*} scroll - The control for the scrollToElement functionality
   * @param {*} searchTerm - The search term
   * @param {*} portfolio - The portfolio id
   */
  loadTable(page, sortBy = this.currentSortBy, order = this.currentOrder, scroll = this.scrollToTable, status = this.currentStatus, searchTerm =this.currentSearchTerm, portfolio = this.portfolioValue) {
    // --------- SEARCH
    let searchParams = this.getSearchParams(page, sortBy, order, searchTerm, status, portfolio); 

    // --------- FETCH DATA
    // fetch json of page of objects, given params
    const baseUrlValue = this.getBaseUrl()?.innerHTML ?? null;
    if (!baseUrlValue) return;

    let url = `${baseUrlValue}?${searchParams.toString()}`
    fetch(url)
      .then(response => response.json())
      .then(data => {
        if (data.error) {
          console.error('Error in AJAX call: ' + data.error);
          return;
        }

        // handle the display of proper messaging in the event that no members exist in the list or search returns no results
        this.updateDisplay(data, this.tableWrapper, this.noDataTableWrapper, this.noSearchResultsWrapper, this.currentSearchTerm);
        // identify the DOM element where the list of results will be inserted into the DOM
        const tbody = this.tableWrapper.querySelector('tbody');
        tbody.innerHTML = '';

        // remove any existing modal elements from the DOM so they can be properly re-initialized
        // after the DOM content changes and there are new delete modal buttons added
        this.unloadModals();

        let dataObjects = this.getDataObjects(data);
        let customTableOptions = this.customizeTable(data);
        this.loadRows(dataObjects, tbody, customTableOptions)

        this.initShowMoreButtons();
        this.initCheckboxListeners();

        this.loadModals(data.page, data.total, data.unfiltered_total);
        this.initializeTooltips();

        // Do not scroll on first page load
        if (scroll)
          scrollToElement('class', this.sectionSelector);
        this.scrollToTable = true;

        // update pagination
        this.updatePagination(
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
    .catch(error => console.error('Error fetching objects:', error));
  }

  loadRows(dataObjects, tbody, customTableOptions) {
    dataObjects.forEach(dataObject => {
      this.addRow(dataObject, tbody, customTableOptions);
    });
  }

  // Add event listeners to table headers for sorting
  initializeTableHeaders() {
    this.tableHeaders.forEach(header => {
      header.addEventListener('click', event => {
        let button = header.querySelector('.usa-table__header__button')
        const sortBy = header.getAttribute('data-sortable');
        let order = 'asc';
        // sort order will be ascending, unless the currently sorted column is ascending, and the user
        // is selecting the same column to sort in descending order
        if (sortBy === this.currentSortBy) {
          order = this.currentOrder === 'asc' ? 'desc' : 'asc';
        }
        // load the results with the updated sort
        this.loadTable(1, sortBy, order);
        // If the click occurs outside of the button, need to simulate a button click in order
        // for USWDS listener on the button to execute.
        // Check first to see if click occurs outside of the button
        if (!button.contains(event.target)) {
            // Simulate a button click
            button.click();
        }
      });
    });
  }

  initializeSearchHandler() {
    this.searchSubmit.addEventListener('click', (e) => {
      e.preventDefault();
      this.currentSearchTerm = this.searchInput.value;
      // If the search is blank, we match the resetSearch functionality
      if (this.currentSearchTerm) {
        showElement(this.resetSearchButton);
      } else {
        hideElement(this.resetSearchButton);
      }
      this.loadTable(1, 'id', 'asc');
      this.resetHeaders();
    });
  }

  initializeStatusToggleHandler() {
    if (this.statusToggle) {
      this.statusToggle.addEventListener('click', () => {
        toggleCaret(this.statusToggle);
      });
    }
  }

  // Add event listeners to status filter checkboxes
  initializeFilterCheckboxes() {
    this.statusCheckboxes.forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        const checkboxValue = checkbox.value;
        
        // Update currentStatus array based on checkbox state
        if (checkbox.checked) {
          this.currentStatus.push(checkboxValue);
        } else {
          const index = this.currentStatus.indexOf(checkboxValue);
          if (index > -1) {
            this.currentStatus.splice(index, 1);
          }
        }

        // Manage visibility of reset filters button
        if (this.currentStatus.length == 0) {
          hideElement(this.resetFiltersButton);
        } else {
          showElement(this.resetFiltersButton);
        }

        // Disable the auto scroll
        this.scrollToTable = false;

        // Call loadTable with updated status
        this.loadTable(1, 'id', 'asc');
        this.resetHeaders();
        this.updateStatusIndicator();
      });
    });
  }

  // Reset UI and accessibility
  resetHeaders() {
    this.tableHeaders.forEach(header => {
      // Unset sort UI in headers
      this.unsetHeader(header);
    });
    // Reset the announcement region
    this.tableAnnouncementRegion.innerHTML = '';
  }

  resetSearch() {
    this.searchInput.value = '';
    this.currentSearchTerm = '';
    hideElement(this.resetSearchButton);
    this.loadTable(1, 'id', 'asc');
    this.resetHeaders();
  }

  initializeResetSearchButton() {
    if (this.resetSearchButton) {
      this.resetSearchButton.addEventListener('click', () => {
        this.resetSearch();
      });
    }
  }

  resetFilters() {
    this.currentStatus = [];
    this.statusCheckboxes.forEach(checkbox => {
      checkbox.checked = false; 
    });
    hideElement(this.resetFiltersButton);

    // Disable the auto scroll
    this.scrollToTable = false;

    this.loadTable(1, 'id', 'asc');
    this.resetHeaders();
    this.updateStatusIndicator();
    // No need to toggle close the filters. The focus shift will trigger that for us.
  }

  initializeResetFiltersButton() {
    if (this.resetFiltersButton) {
      this.resetFiltersButton.addEventListener('click', () => {
        this.resetFilters();
      });
    }
  }

  updateStatusIndicator() {
    this.statusIndicator.innerHTML = '';
    // Even if the element is empty, it'll mess up the flex layout unless we set display none
    hideElement(this.statusIndicator);
    if (this.currentStatus.length)
      this.statusIndicator.innerHTML = '(' + this.currentStatus.length + ')';
      showElement(this.statusIndicator);
  }

  closeFilters() {
    if (this.statusToggle.getAttribute("aria-expanded") === "true") {
      this.statusToggle.click();
    }
  }

  initializeAccordionAccessibilityListeners() {
    // Instead of managing the toggle/close on the filter buttons in all edge cases (user clicks on search, user clicks on ANOTHER filter,
    // user clicks on main nav...) we add a listener and close the filters whenever the focus shifts out of the dropdown menu/filter button.
    // NOTE: We may need to evolve this as we add more filters.
    document.addEventListener('focusin', (event) => {
      const accordion = document.querySelector('.usa-accordion--select');
      const accordionThatIsOpen = document.querySelector('.usa-button--filter[aria-expanded="true"]');
      
      if (accordionThatIsOpen && !accordion.contains(event.target)) {
        this.closeFilters();
      }
    });

    // Close when user clicks outside
    // NOTE: We may need to evolve this as we add more filters.
    document.addEventListener('click', (event) => {
      const accordion = document.querySelector('.usa-accordion--select');
      const accordionThatIsOpen = document.querySelector('.usa-button--filter[aria-expanded="true"]');
    
      if (accordionThatIsOpen && !accordion.contains(event.target)) {
        this.closeFilters();
      }
    });
  }
}
