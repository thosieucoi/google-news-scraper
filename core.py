from __future__ import print_function

import errno
import logging
import os
import random
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from mtranslate import translate
from slugify import slugify

from constants import *
from extract_content import get_content, get_title

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def parallel_function(f, sequence, num_threads=None):
    from multiprocessing import Pool
    pool = Pool(processes=num_threads)
    result = pool.map(f, sequence)
    cleaned = [x for x in result if x is not None]
    pool.close()
    pool.join()
    return cleaned


class UrlForge:

    def __init__(self):
        self.num_calls = 0
        self.google_news_url = 'https://www.google.co.jp/search?q={}&hl=ja&source=lnt&' \
                               'tbs=cdr%3A1%2Ccd_min%3A{}%2Ccd_max%3A{}&tbm=nws&start={}'

    def forge(self, q, start, year_start, year_end):
        self.num_calls += 1
        return self.google_news_url.format(q.replace(' ', '+'), str(year_start), str(year_end), start)


def extract_links(content):
    soup = BeautifulSoup(content.decode('utf8'), 'html.parser')  # _sQb top _vQb _mnc
    links_list = [(v.attrs['href'], v.text) for v in soup.find_all('a', {'class': ['l lLrAF']})]
    dates_list = [a.find('span', {'class': ['f nsa fwzPFf']}).text for a in soup.find_all('div', {'class': 'slp'})]
    assert len(links_list) == len(dates_list)
    output = []
    for (link, date) in zip(links_list, dates_list):
        output.append({
            'link': link[0],
            'title': link[1],
            'date': date
        })
    return output


def google_news_run(keyword, limit=10, year_start=2010, year_end=2011, sleep_time_every_ten_articles=0):
    num_articles_index = 0
    ua = UserAgent()
    uf = UrlForge()
    result = []
    while num_articles_index < limit:
        url = uf.forge(keyword, num_articles_index, year_start, year_end)
        logging.info('[Google News] Total num calls = %s (for keyword).' % uf.num_calls)
        logging.info('[Google News] %s.' % url)
        headers = {'User-Agent': ua.chrome}
        try:
            response = requests.get(url, headers=headers, timeout=20)
            links = extract_links(response.content)

            nb_links = len(links)
            if nb_links == 0 and num_articles_index == 0:
                raise Exception(
                    'No results fetched. Either the keyword is wrong '
                    'or you have been banned from Google. Retry tomorrow '
                    'or change of IP Address.')

            if nb_links == 0:
                print('No more news to read for keyword {}.'.format(keyword))
                break

            for i in range(nb_links):
                cur_link = links[i]
                logging.info('|- {} ({})'.format(cur_link['title'], cur_link['date']))
            result.extend(links)
        except requests.exceptions.Timeout:
            logging.warning('Google news TimeOut. Maybe the connection is too slow. Skipping.')
            pass
        num_articles_index += 10
        logging.info('Program is going to sleep for {} seconds.'.format(sleep_time_every_ten_articles))
        time.sleep(sleep_time_every_ten_articles)
    return result


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def get_keywords():
    response = requests.get('http://www.generalecommerce.com/clients/broadcastnews_tv/category_list_js.html',
                            timeout=20)
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, 'html.parser')
    keywords = [l.replace('news', '') for l in
                set([v.text for v in soup.find_all('td', {'class': 'devtableitem'}) if 'http' not in v.text])]
    assert len(keywords) > 0

    random.shuffle(keywords)
    for keyword in keywords:
        japanese_keyword = translate(keyword, 'ja')
        logging.info('[Google Translate] {} -> {}'.format(keyword, japanese_keyword))
        if re.search('[a-zA-Z]', japanese_keyword):  # we don't want that: Fed watch -> Fed時計
            continue
        yield japanese_keyword


def run(keywords=None):
    if keywords is None:
        keywords = get_keywords()
    for keyword in keywords:
        logging.info('[Google News] -> FETCHING NEWS FOR KEYWORD [{}].'.format(keyword))
        generate_articles(keyword, year_end=datetime.now().year)


def generate_articles(keyword, year_start=2010, year_end=2019, limit=data.ARTICLE_COUNT_LIMIT_PER_KEYWORD):
    tmp_news_folder = 'data/{}/news'.format(keyword)
    mkdir_p(tmp_news_folder)

    tmp_link_folder = 'data/{}/links'.format(keyword)
    mkdir_p(tmp_link_folder)

    json_file = '{}/{}_{}_{}_links.json'.format(tmp_link_folder, keyword, year_start, year_end)
    if os.path.isfile(json_file):
        logging.info('Google news links for keyword [{}] have been fetched already.'.format(keyword))
        with open(json_file, 'r', encoding='utf8') as r:
            links = json.load(fp=r)
        logging.info('Found {} links.'.format(len(links)))
    else:
        links = google_news_run(keyword=keyword,
                                limit=limit,
                                year_start=year_start,
                                year_end=year_end,
                                sleep_time_every_ten_articles=data.SLEEP_TIME_EVERY_TEN_ARTICLES_IN_SECONDS)
        with open(json_file, 'w', encoding='utf8') as w:
            json.dump(fp=w, obj=links)
    if int(data.RUN_POST_PROCESSING):
        retrieve_data_from_links(links, tmp_news_folder)


def retrieve_data_for_link(param):
    logging.info('retrieve_data_for_link - param = {}'.format(param))
    (full_link, tmp_news_folder) = param
    link = full_link['link']
    google_title = full_link['title']
    link_datetime = full_link['date']
    compliant_filename_for_link = slugify(link)
    max_len = 100
    if len(compliant_filename_for_link) > max_len:
        logging.info('max length exceeded for filename ({}). Truncating.'.format(compliant_filename_for_link))
        compliant_filename_for_link = compliant_filename_for_link[:max_len]
    json_file = '{}/{}.json'.format(tmp_news_folder, compliant_filename_for_link)
    already_fetched = os.path.isfile(json_file)
    if not already_fetched:
        try:
            html = download_html_from_link(link)
            soup = BeautifulSoup(html, 'html.parser')
            content = get_content(soup)
            full_title = complete_title(soup, google_title)
            article = {
                'link': link,
                'title': full_title,
                'content': content,
                'date': link_datetime
            }
            with open(json_file, 'w', encoding='utf8') as w:
                json.dump(fp=w, obj=article)
        except Exception as e:
            logging.error(e)
            logging.error('ERROR - could not download article with link {}'.format(link))
            pass


def retrieve_data_from_links(full_links, tmp_news_folder):
    num_threads = data.LINKS_POST_PROCESSING_NUM_THREADS
    if num_threads > 1:
        inputs = [(full_links, tmp_news_folder) for full_links in full_links]
        parallel_function(retrieve_data_for_link, inputs, num_threads)
    else:
        for full_link in full_links:
            retrieve_data_for_link((full_link, tmp_news_folder))


def download_html_from_link(link, params=None, fail_on_error=True):
    try:
        logging.info('Get -> {} '.format(link))
        response = requests.get(link, params, timeout=20)
        if fail_on_error and response.status_code != 200:
            raise Exception('Response code is not [200]. Got: {}'.format(response.status_code))
        else:
            logging.info('Download successful [OK]')
        return response.content
    except:
        if fail_on_error:
            raise
        return None


def update_title(soup, google_article_title):
    fail_to_update = False
    if '...' not in google_article_title:
        # we did not fail because the google title was already valid.
        return google_article_title, fail_to_update
    truncated_title = google_article_title[:-4]  # remove ' ...' at the end.
    title_list = [v.text for v in soup.find_all('h1') if len(v.text) > 0]
    for title in title_list:
        if truncated_title in title:
            # we succeeded here because we found the original title
            return title, fail_to_update
    fail_to_update = True
    return google_article_title, fail_to_update


def complete_title(soup, google_article_title):
    # soup.contents (show without formatting).
    full_title, fail_to_update = update_title(soup, google_article_title)
    if full_title != google_article_title:
        logging.debug('Updated title: old is [{}], new is [{}]'.format(google_article_title, full_title))
    else:
        if fail_to_update:
            logging.debug('Could not update title with Google truncated title trick.')
            full_title = get_title(soup)
            logging.debug('Found it anyway here [{}]'.format(full_title))
        else:
            logging.debug('Nothing to do for title [{}]'.format(full_title))
    return full_title.strip()
