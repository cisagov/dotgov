
import { showElement, hideElement } from './helpers.js';
import { BaseTable } from './table-base.js';

export class MemberDomainsTable extends BaseTable {

  constructor() {
    super('member-domain');
    this.displayName = "domain";
    this.currentSortBy = 'name';
  }
  getBaseUrl() {
    return document.getElementById("get_member_domains_json_url");
  }
  getDataObjects(data) {
    return data.domains;
  }
  addRow(dataObject, tbody, customTableOptions) {
    const domain = dataObject;
    const row = document.createElement('tr');
    row.innerHTML = `
      <th scope="row" role="rowheader" data-label="Domain name">
        ${domain.name}
      </th>
    `;
    tbody.appendChild(row);
  }
  updateDisplay = (data, dataWrapper, noDataWrapper, noSearchResultsWrapper) => {
    const { unfiltered_total, total } = data;
    const searchSection = document.getElementById('edit-member-domains__search');
    if (!searchSection) console.warn('MemberDomainsTable updateDisplay expected an element with id edit-member-domains__search but none was found');
    if (unfiltered_total) {
      showElement(searchSection);
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
      hideElement(searchSection);
      hideElement(dataWrapper);
      hideElement(noSearchResultsWrapper);
      showElement(noDataWrapper);
    }
  };
}
  
export function initMemberDomainsTable() {
  document.addEventListener('DOMContentLoaded', function() {
      const isMemberDomainsPage = document.getElementById("member-domains") 
      if (isMemberDomainsPage){
        const memberDomainsTable = new MemberDomainsTable();
        if (memberDomainsTable.tableWrapper) {
          // Initial load
          memberDomainsTable.loadTable(1);
        }
      }
    });
}
