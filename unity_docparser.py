"""
Parses Unity's HTML documents into a .xml file.

Currently only the docs for UnityEngine.dll are parsed,
plus there some omissions due to laziness.

Aleksi Pekkala
2.2.2014
"""

import re
import gevent
from gevent import monkey
monkey.patch_all()
import urllib2
from lxml import html
from lxml import etree


URL_ROOT = 'http://docs.unity3d.com/Documentation/ScriptReference/'
PREFIX_HEADER = 'UnityEngine.'
PREFIX_CLASS = 'T:'
PREFIX_METHOD = 'M:'
PREFIX_CONSTRUCTOR = 'C:'
PREFIX_VARIABLE = 'P:'


def main():
    doc_file, assembly_name = "UnityEngine.xml", "UnityEngine"
    root_elem = etree.Element('doc')

    # Append the 'assembly' and 'members' subelements:
    assembly_elem = etree.SubElement(root_elem, 'assembly')
    assembly_name_elem = etree.SubElement(assembly_elem, 'name')
    assembly_name_elem.text = assembly_name
    members_elem = etree.SubElement(root_elem, 'members')

    # Parse class URLs:
    class_urls = set()
    root = html.parse(URL_ROOT)
    classes = root.xpath('//li[@class="classRuntime"]')
    for li in classes:
        try:
            class_urls.add(URL_ROOT + li[0].attrib['href'])
        except (KeyError, IndexError):
            continue

    # Load class pages in a somewhat parallel fashion:
    pages = [gevent.spawn(urllib2.urlopen, url) for url in class_urls]
    gevent.joinall(pages)

    for page in pages:
        for elem in parse_class(page.value):
            members_elem.append(elem)

    tree = etree.ElementTree(root_elem)
    tree.write(doc_file, pretty_print=True)


def parse_class(page):
    """Parses the docs of a single class.

    :param page: The page to scrape.
    :returns: a generator which yields xml 'member' elements.
    """
    print "parsing CLASS @ " + page.url

    # Don't parse inherited members:
    root = html.parse(page).xpath('//div[@id="mainContainer"]')[0]

    cnd = 'div[@class="script-section-hardheading"]/following-sibling::div'
    for elem in root.xpath(cnd):
        root.remove(elem)

    get_subheadings = lambda x: root.xpath('div[normalize-space(.)="%s"]' % x)
    header = PREFIX_HEADER + root.xpath('*[@class="heading"]')[0].text_content().strip()

    # Parse class:

    class_name = PREFIX_CLASS + header
    class_description = ""
    class_tree = generate_xml_tree(class_name, class_description)
    yield class_tree

    # Parse constructors:

    cons_headings = get_subheadings('Constructors')
    if cons_headings:
        cons_table = cons_headings[0].getnext()
        if cons_table is not None and cons_table.tag == 'table':
            cons_paths = cons_table.xpath('tr/th/a/@href')
            for path in cons_paths:
                for cons_elem in parse_constructor(URL_ROOT + path):
                    yield cons_elem

    # Parse variables:

    var_headings = get_subheadings('Variables')
    if var_headings:
        var_table = var_headings[0].getnext()
        if var_table is not None and var_table.tag == 'table':
            var_prefix = PREFIX_VARIABLE + header + "."

            for row in var_table.getchildren():
                var_name = row.xpath('.//a')[0].text.strip()
                var_desc = row.getchildren()[-1].text_content().strip()
                elem_name = var_prefix + var_name
                yield generate_xml_tree(elem_name, var_desc)

    # Parse functions and static functions:

    func_rows = []
    for headings in ['Functions', 'Static Functions']:
        heading_elems = get_subheadings(headings)
        if heading_elems:
            func_table = heading_elems[0].getnext()
            if func_table is not None and func_table.tag == 'table':
                func_rows.extend(func_table.getchildren())


    func_urls = [URL_ROOT + r.xpath('.//a/@href')[0] for r in func_rows]
    func_pages = [gevent.spawn(urllib2.urlopen, url) for url in func_urls]
    gevent.joinall(func_pages)
    for func_page in func_pages:
        for func_elem in parse_function(func_page.value):
            yield func_elem

    return


def parse_constructor(url):
    """Parses the docs for constructors.

    :param url: url to the constructor docs.
    :return: a generator yielding xml 'member' elements.
    """
    print "parsing CONSTRUCTOR @ " + url

    page = html.parse(url)
    root = page.xpath('//div[@id="mainContainer"]')[0]
    for section in root.xpath('div[@class="section"]'):
        name_elem = section.xpath('div[1]/div[1]/div[2]')[0]
        name = name_elem.text_content().strip().replace(';', '')

        desc = ""
        try:
            desc_header_elem = section.xpath(".//div[.='Description']")[0]
            desc = desc_header_elem.getnext().text_content().strip()
        except IndexError:
            print "Failed to parse constructor description at " + url

        params = []
        for param in name.split(','):
            param_name = param.split()[-1]
            if param_name.endswith(');'):
                param_name = param_name[:-2]
            params.append({'name': param_name})

        elem_name = PREFIX_CONSTRUCTOR + PREFIX_HEADER + name
        yield generate_xml_tree(elem_name, desc, params=params)


def parse_function(page):
    """Parses the docs for a function.

    :param page: function docs page.
    :returns: a generator yielding xml 'member' elements.
    """
    print "parsing FUNCTION @ " + page.url

    root = html.parse(page).xpath('//div[@id="mainContainer"]')[0]
    func_header = root.xpath('*[@class="heading"]/a')[0].text.strip()
    header = PREFIX_METHOD + PREFIX_HEADER + func_header + '.'

    for section in root.xpath('div[@class="section"]'):
        name = None
        try:
            name_raw = section.xpath("div[1]/div[1]/div[2]")[0].text_content()
            name_raw = ''.join(name_raw.splitlines())
            name = re.findall(r'\w*\(.*\)', name_raw)[0]
        except IndexError:
            print "Failed to parse function from " + page.url
            return

        desc = ""
        try:
            desc_block = section.xpath('div[div[1][.="Description"]]')[0]
            desc = desc_block.xpath("p")[0].text_content().strip()
        except IndexError:
            print 'Failed to parse description for function ' + name

        params = []
        try:
            param_rows = section.xpath('div[div[1][.="Parameters"]]//tr')
            for param_row in param_rows:
                params.append({
                    'name': param_row[0].text_content().strip(),
                    'text': param_row[1].text_content().strip()
                })
        except IndexError:
            print 'Failed to parse params for function ' + name

        yield generate_xml_tree(header + name, desc, params=params)



def generate_xml_tree(name, summary='', remarks='', params=[]):
    """Generates an xml tree from a single doc block.

    :returns xml tree, where parent element is 'member'
    """
    root = etree.Element('member')
    root.set('name', name)
    etree.SubElement(root, 'summary').text = summary
    etree.SubElement(root, 'remarks').text = remarks
    for param in params:
        param_elem = etree.SubElement(root, 'param')
        param_elem.set('name', param['name'])
        param_elem.text = param['text'] if 'text' in param else ''
    return root



if __name__ == '__main__':
    main()