import requests
from BeautifulSoup import BeautifulSoup
from re import sub
from decimal import Decimal
import feedparser
import urlparse
from address import AddressParser

# URL formats for ISBE report sections
D2_MAIN_PAGE = ('http://elections.state.il.us/CampaignDisclosure/D2Quarterly.'
                'aspx?id=%s')
INDIV_CONTRIB = ('http://www.elections.il.gov/CampaignDisclosure/ItemizedContr'
                 'ibPrint.aspx?FiledDocID=%s&ContributionType=Individual+Contr'
                 'ibutions&Archived=True&ItemizedContribFrom=D2Quarterly.aspx&'
                 'OrderBy=Last+or+Only+Name+-+A+to+Z')
TRANSFER_IN = ('http://www.elections.il.gov/CampaignDisclosure/ItemizedContrib'
               'Print.aspx?FiledDocID=%s&ContributionType=Transfers+In&Archive'
               'd=True&ItemizedContribFrom=D2Quarterly.aspx&OrderBy=Last+or+On'
               'ly+Name+-+A+to+Z')

# ISBE Recently-filed reports
ISBE_REPORTS_FEED = 'http://elections.state.il.us/rss/SBEReportsFiledWire.aspx'


# Scraper for the Reports Filed list
def scrape_reports_filed(reports_url=ISBE_REPORTS_FEED):
    """
    Reads the reports_filed list and returns list of parsed reports metadata.
    """
    feed = feedparser.parse(reports_url)
    reports_list = []
    for f in feed['entries']:
        report_date = f['summary'].split('<br />')[2].split(' ')[0]
        if 'href' not in f['links'][0]:
            continue
        report_url = f['links'][0]['href']
        parsed_url = urlparse.parse_qs(urlparse.urlparse(report_url).query)
        if report_url.startswith(
            'http://www.elections.il.gov/CampaignDisclosure/CDPdfViewer'
                '.aspx'):
            report_type = 'PDF'
            report_id = parsed_url['FiledDocID'][0]
        elif report_url.startswith(
                'http://www.elections.il.gov/CampaignDisclosure/D2'):
            report_type = 'D2Q'
            report_id = parsed_url['id'][0]
        elif report_url.startswith(
                'http://www.elections.il.gov/CampaignDisclosure/A1'):
            report_type = 'A1'
            report_id = parsed_url['FiledDocID'][0]
        else:
            report_type = 'UNK'  # Unknown/unhandled report type
            report_id = -1
        reports_list.append({
            'report_id': report_id,
            'report_type': report_type,
            'report_url': report_url,
            'report_date': report_date
        })
    return reports_list


# Scraper interfaces for specific report types
def scrape_a1(report_id, report_url, report_date):
    """
    Given a report_id, attempts to download it and scrape it.
    Unlike with D2s, we need the URL and date up front, because they're tougher
    to extract from the body of the A1 report itself.
    """
    req = requests.get(report_url)
    soups = [BeautifulSoup(req.text)]
    page_index = 1
    keep_going = True
    while keep_going:
        paginated_url = '%s&pageindex=%d' % (report_url, page_index)
        page_index += 1
        r = requests.get(paginated_url)
        p_soup = BeautifulSoup(r.text)
        if p_soup.findAll('td', 'tdA1ListContributor'):
            soups.append(p_soup)
        else:
            keep_going = False
    return _process_a1_page(
        soups, report_url, report_id, report_date.split('/'))


def scrape_d2(report_id):
    """
    Given a report_id, attempts to download it and scrape it, saving all data
    into db as it goes. If delete_first is True, deletes all contribs
    associated with this report before beginning (to avoid duplicate contribs).
    """
    ind_url = INDIV_CONTRIB % report_id
    xfer_url = TRANSFER_IN % report_id
    meta_url = D2_MAIN_PAGE % report_id
    ind_req = requests.get(ind_url)
    xfer_req = requests.get(xfer_url)
    meta_req = requests.get(meta_url)
    ind_soup = BeautifulSoup(ind_req.text)
    xfer_soup = BeautifulSoup(xfer_req.text)
    meta_soup = BeautifulSoup(meta_req.text)

    ind_contribs = _process_d2_page(ind_soup, report_id, False)
    xfer_contribs = _process_d2_page(xfer_soup, report_id, True)
    metadata = _process_d2_metadata(meta_soup)

    return (ind_contribs, xfer_contribs, metadata)


# Scraper "backends" for specific report types
def _process_a1_page(soups, url, report_id, report_date):
    """
    Does the heavy lifting of actually looking at the report HTML, extracting
    the data we care about and saving it to the db.
    """
    report_contribs = []
    name = soups[0].findAll('span', id='ctl00_ContentPlaceHolder1_lblName')
    if name:
        cmte_name = name[0].contents[0]
    cmte_id = None
    for f in soups[0].findAll('td', 'tdA1List'):
        if f['headers'] == 'ctl00_ContentPlaceHolder1_thRecievedBy':
            cmte_url = f.findAll('a')[0]['href']
            cmte_id = cmte_url.replace('CommitteeDetail.aspx?id=', '')
            break
    if cmte_id:
        for soup in soups:
            for tr in soup.findAll('tr', {
                'class': [
                    'SearchListTableRow',
                    'SearchListTableRowAlternating']}):
                occupation = ''
                employer = ''
                for ct in tr.findAll('td', 'tdA1ListContributor'):
                    if ct.findAll('span')[0].contents:
                        donor = ct.findAll('span')[0].contents[0]
                        for str in ct.findAll('span')[0].contents[1:]:
                            if isinstance(str, basestring):
                                    if str.startswith('Occupation:'):
                                        occupation = str.replace(
                                            'Occupation:', '').strip()
                                    elif str.startswith('Employer:'):
                                        employer = str.replace(
                                            'Employer:', '').strip()
                vendor_address = ''
                i = 0
                for addr in tr.findAll('td', 'tdA1ListAddress'):
                    if addr.findAll('span')[0].contents:
                        address_str = ''
                        for ad in addr.findAll('span'):
                            address_str = '%s, %s' % (address_str, ad)
                        if i == 0:
                            donor_address_str = _clean_a1_address(address_str)
                        else:
                            vendor_address = _clean_a1_address(address_str)
                    i += 1
                i = 0
                is_transfer_in = False
                vendor_name = ''
                description = ''
                for misc in tr.findAll('td', 'tdA1List'):
                    if i == 0 and misc.findAll('span')[0].contents:
                        amount = misc.findAll('span')[0].contents[0]
                        date = misc.findAll('span')[0].contents[2]
                    elif i == 1 and misc.findAll('span')[0].contents:
                        if misc.findAll('span')[0].contents[0] == '2A':
                            is_transfer_in = True
                    elif i == 2 and misc.findAll('span')[0].contents:
                        description = misc.findAll('span')[0].contents[0]
                    elif i == 3 and misc.findAll('span')[0].contents:
                        vendor_name = misc.findAll('span')[0].contents[0]
                    i += 1
                amount = amount.replace('$', '').replace(',', '')
                parsed_address = _parse_address_string(donor_address_str)
                (first_name, last_name) = _parse_name_string(donor)
                contrib_obj = {
                    'donor_name_str': donor,
                    'parsed_firstname': first_name,
                    'parsed_lastname': last_name,
                    'occupation': occupation,
                    'employer': employer,
                    'address_str': donor_address_str,
                    'parsed_address': parsed_address,
                    'amount': Decimal(amount),
                    'description': description,
                    'vendor_name': vendor_name,
                    'vendor_address': vendor_address,
                    'is_transfer_in': is_transfer_in,
                    'date': '%s-%s-%s' % (
                        date.split('/')[2],
                        date.split('/')[0],
                        date.split('/')[1])
                }
                report_contribs.append(contrib_obj)
        return {
            'contribs': report_contribs,
            'committee_id': cmte_id,
            'committee_name': cmte_name
        }
    else:
        print 'Invalid report: %s' % url


def _process_d2_page(soup_obj, report_id, is_transfer_in):
    """
    Does the heavy lifting of actually looking at the report HTML, extracting
    the data we care about and saving it to the db.
    """
    processed_contribs = []

    # Iterate over all rows in table, and process each one
    for row in soup_obj('tr'):
        valid_row = False
        # Cell classes that contain data we care about
        contrib = {
            'tdContributedBy': '',
            'tdContribAddress': '',
            'tdContribAmount': 0,
            'tdDescription': '',
            'tdVendorName': '',
            'tdVendorAddress': '',
            'date': ''
        }
        # For each cell, see if it's got something we care about and extract it
        for col in row('td'):
            key = col.attrs[0][1]
            # Validity check - once we see some classes we care about, it's ok
            if key in contrib.keys():
                valid_row = True
                val = str(col.contents[0]).replace('<span>', '').replace(
                    '</span>', '').strip()
                if key == 'tdContribAmount':
                    try:
                        (amount, contrib['date']) = val.split('<br />')
                        contrib['tdContribAmount'] = Decimal(sub(
                            r'[^\d.]', '', amount))
                    except Exception:
                        continue
                else:
                    contrib[key] = val
            else:
                continue
        if not valid_row:
            continue
        try:
            employer = contrib['tdContributedBy'].split('<br />')[2].split(
                'Employer:')[1].strip()
            occupation = contrib['tdContributedBy'].split(
                '<br />')[1].split('Occupation:')[1].strip()
        except Exception:
            employer = ''
            occupation = ''
        amount = contrib['tdContribAmount']
        address_str = contrib['tdContribAddress'].replace(
            '<br />', ', ').strip()
        parsed_address = _parse_address_string(address_str)
        name = contrib['tdContributedBy'].split('<br />')[0].strip()
        (first_name, last_name) = _parse_name_string(name)
        date = '%s-%s-%s' % (
            contrib['date'].split('/')[2],
            contrib['date'].split('/')[0],
            contrib['date'].split('/')[1])
        contrib_obj = {
            'full_name': name,
            'parsed_firstname': first_name,
            'parsed_lastname': last_name,
            'occupation': occupation,
            'employer': employer,
            'address_1': parsed_address.address_1,
            'address_2': parsed_address.apartment,
            'address_string': address_str,
            'city': parsed_address.city,
            'state': parsed_address.state,
            'zipcode': parsed_address.zip,
            'amount': amount,
            'description': contrib['tdDescription'].strip(),
            'vendor_name': contrib['tdVendorName'].strip(),
            'vendor_address': contrib['tdVendorAddress'].strip(),
            'is_transfer_in': is_transfer_in,
            'date': date
        }
        processed_contribs.append(contrib_obj)
    return processed_contribs


def _process_d2_metadata(soup):
    """
    Extracts a variety of top-line numbers from the main page of a D2 report.
    """
    meta_labels = {
        'lblRptPd': 'Report Period',
        'lblIndivContribI': 'Individual Contributions (Itemized)',
        'lblIndivContribNI': 'Individual Contributions (Non-Itemized)',
        'lblXferInI': 'Transfers In (Itemized)',
        'lblXferInNI': 'Transfers In (Non-Itemized)',
        'lblLoanRcvI': 'Loans Received (Itemized)',
        'lblLoanRcvNI': 'Loans Received (Non-Itemized)',
        'lblOtherRctI': 'Other Receipts (Itemized)',
        'lblOtherRctNI': 'Other Receipts (Non-Itemized)',
        'lblTotalReceipts': 'Total Receipts',
        'lblInKindI': 'In-Kind Receipts (Itemized)',
        'lblInKindNI': 'In-Kind Receipts (Non-Itemized)',
        'lblTotalInKind': 'Total In-Kind Receipts',
        'lblXferOutI': 'Transfers Out (Itemized)',
        'lblXferOutNI': 'Transfers Out (Non-Itemized)',
        'lblLoanMadeI': 'Loans Made (Itemized)',
        'lblLoanMadeNI': 'Loans Made (Non-Itemized)',
        'lblExpendI': 'Expenditures (Itemized)',
        'lblExpendNI': 'Expenditures (Non-Itemized)',
        'lblItemizedExpenditureIndependentAmount': (
            'Independent Expenditures (Itemized)'),
        'lblNotItemizedExpenditureIndependentAmount': (
            'Independent Expenditures (Non-Itemized)'),
        'lblTotalExpend': 'Total Expenditures',
        'lblDebtsI': 'Debts (Itemized)',
        'lblDebtsNI': 'Debts (Non-Itemized)',
        'lblTotalDebts': 'Total Debts',
        'lblBegFundsAvail': 'Beginning Balance',
        'lblTotalReceiptsTot': 'Total Receipts',
        'lblTotalExpendTot': 'Total Expenditures',
        'lblEndFundsAvail': 'Ending Balance',
        'lblTotalInvest': 'Investment Total'
    }
    return_meta = {}
    for span in soup('span', 'BaseText'):
        label_id = span['id'].replace('ctl00_ContentPlaceHolder1_', '')
        if label_id in meta_labels:
            # Special-case the reporting period; it's the only non-monetary key
            if label_id == 'lblRptPd':
                return_meta[meta_labels[label_id]] = span.text
                continue
            try:
                return_meta[meta_labels[label_id]] = Decimal(
                    sub(r'[^\d.]', '', span.text))
            except ValueError:
                print 'Cant convert %s to float' % span.text
                return_meta[meta_labels[label_id]] = span.text
    return return_meta


def _parse_address_string(address_str):
    """
    Convenience wrapper around AddressParser. Primarily handles lack of 9-digit
    zipcode support and standardizes address_1 creation.
    """
    ap = AddressParser()
    parsed_address = ap.parse_address(sub('-[0-9]{4}$', '', address_str))
    found_fields = []
    if parsed_address.house_number:
        found_fields.append(parsed_address.house_number)
    if parsed_address.street_prefix:
        found_fields.append(parsed_address.street_prefix)
    if parsed_address.street:
        found_fields.append(parsed_address.street)
    if parsed_address.street_suffix:
        found_fields.append(parsed_address.street_suffix)
    parsed_address.address_1 = ' '.join(found_fields)
    return parsed_address


def _parse_name_string(name_str):
    """
    Return a 2-tuple of (first_name, last_name) from name_str. This is mostly
    guesswork, since a lot of "names" are actually corporation or committee
    names, and people's names don't always - but often - follow the standard
    last_name, first_name format in the reports.
    """
    first_name = ''
    last_name = ''
    split_name = name_str.split(',', 1)
    if len(split_name) > 1:
        first_name = split_name[1]
        last_name = split_name[0]
    else:
        first_name = name_str
    return (first_name, last_name)


def _clean_a1_address(address_str):
    """
    Handles standard cleanup of weirdo A1 address.
    """
    address_str = address_str.replace('<br /><br />', ', ')
    address_str = address_str.replace('<br />', ', ')
    address_str = address_str.replace('<span>', '')
    address_str = address_str.replace('</span>', '')
    return address_str[2:]


if __name__ == "__main__":
    print 'Looking for recent reports, and printing out details of A1s:'
    for report in scrape_reports_filed():
        if report['report_type'] == 'A1':
            print scrape_a1(
                report['report_id'],
                report['report_url'],
                report['report_date'])
