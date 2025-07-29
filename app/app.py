from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
from concurrent.futures import ThreadPoolExecutor
import asyncio
import aiohttp
from functools import partial
import os
import json
import hashlib
from datetime import datetime, timedelta

app = Flask(__name__)

# 创建线程池
executor = ThreadPoolExecutor(max_workers=10)

# 网站基础URL
BASE_URL = "https://www.kanshulao.com"
SEARCH_URL = "https://www.sososhu.com"

# 缓存目录
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def get_cache_key(url):
    """生成缓存文件名"""
    return hashlib.md5(url.encode('utf-8')).hexdigest() + '.json'

def get_cache_path(url):
    """获取缓存文件路径"""
    return os.path.join(CACHE_DIR, get_cache_key(url))

def save_to_cache(url, data):
    """保存数据到缓存"""
    cache_path = get_cache_path(url)
    cache_data = {
        'timestamp': datetime.now().isoformat(),
        'data': data
    }
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

def load_from_cache(url, expiry_hours=24):
    """从缓存加载数据，如果缓存过期或不存在则返回None"""
    cache_path = get_cache_path(url)
    if not os.path.exists(cache_path):
        return None
    
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        
        # 检查缓存是否过期
        timestamp = datetime.fromisoformat(cache_data['timestamp'])
        if datetime.now() - timestamp > timedelta(hours=expiry_hours):
            os.remove(cache_path)  # 删除过期缓存
            return None
            
        return cache_data['data']
    except Exception as e:
        print(f"读取缓存失败: {e}")
        return None

def clear_cache():
    """清空所有缓存"""
    for filename in os.listdir(CACHE_DIR):
        file_path = os.path.join(CACHE_DIR, filename)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            print(f"删除缓存文件失败 {file_path}: {e}")

def fetch_page(url):
    """
    获取网页内容（带缓存）
    """
    # 先尝试从缓存加载
    cached_data = load_from_cache(url)
    if cached_data is not None:
        print(f"从缓存加载: {url}")
        return cached_data
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers)
        response.encoding = 'utf-8'
        content = response.text
        # 保存到缓存
        save_to_cache(url, content)
        return content
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

@app.route('/')
def index():
    """
    首页 - 显示推荐书籍
    """
    # 使用线程池执行耗时操作
    future = executor.submit(fetch_page, BASE_URL)
    html_content = future.result()
    
    if not html_content:
        return "无法获取首页内容"
    
    books = []
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 查找经典推荐部分
    recommend_section = soup.find('h2', class_='layout-tit', string='经典推荐')
    if recommend_section:
        ul_list = recommend_section.find_next('ul', class_='txt-list')
        if ul_list:
            for li in ul_list.find_all('li'):
                spans = li.find_all('span')
                if len(spans) >= 3:
                    category = spans[0].get_text(strip=True)
                    book_link = spans[1].find('a')
                    author_link = spans[2].find('a')
                    
                    if book_link and author_link:
                        book = {
                            'category': category,
                            'title': book_link.get_text(strip=True),
                            'book_url': BASE_URL + book_link['href'] if book_link['href'].startswith('/') else book_link['href'],
                            'author': author_link.get_text(strip=True),
                            'author_url': BASE_URL + author_link['href'] if author_link['href'].startswith('/') else author_link['href']
                        }
                        books.append(book)
    
    return render_template('index.html', books=books)

@app.route('/search')
def search():
    """
    搜索书籍
    """
    keyword = request.args.get('q', '')
    if not keyword:
        return render_template('search.html', books=[], keyword='')
    
    # 构造搜索URL
    search_url = f"{SEARCH_URL}/?q={urllib.parse.quote(keyword)}&site=lkyuedu"
    
    # 使用线程池执行耗时操作
    future = executor.submit(fetch_page, search_url)
    html_content = future.result()
    
    if not html_content:
        return render_template('search.html', books=[], keyword=keyword)
    
    books = []
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 解析搜索结果
    book_items = soup.find_all('div', class_='item')
    for item in book_items:
        try:
            image_div = item.find('div', class_='image')
            dl = item.find('dl')
            
            if image_div and dl:
                # 获取书籍链接和封面
                book_link = image_div.find('a')
                img = image_div.find('img')
                
                # 获取书名和作者
                dt = dl.find('dt')
                if dt:
                    author_span = dt.find('span')
                    title_link = dt.find('a')
                    
                    if book_link and title_link:
                        book = {
                            'title': title_link.get_text(strip=True),
                            'book_url': book_link['href'],
                            'author': author_span.get_text(strip=True) if author_span else '未知',
                            'cover': img['src'] if img else ''
                        }
                        books.append(book)
        except Exception as e:
            print(f"解析搜索结果出错: {e}")
            continue
    
    return render_template('search.html', books=books, keyword=keyword)

@app.route('/book')
def book_detail():
    """
    书籍详情页 - 显示章节列表和详细信息
    """
    book_url = request.args.get('url', '')
    if not book_url:
        return "无效的书籍链接"
    
    # 判断书籍来源网站，使用对应的BASE_URL
    if "lkyuedu.com" in book_url:
        site_base_url = "https://www.lkyuedu.com"
    else:
        site_base_url = BASE_URL
    
    # 处理相对路径URL
    if book_url.startswith('/'):
        full_book_url = site_base_url + book_url
    elif not book_url.startswith('http'):
        full_book_url = site_base_url + '/' + book_url
    else:
        full_book_url = book_url
    
    # 使用线程池执行耗时操作
    future = executor.submit(fetch_page, full_book_url)
    html_content = future.result()
    
    if not html_content:
        return "无法获取书籍内容"
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 从meta标签中提取书籍详细信息
    book_info = {}
    
    # 提取OG信息
    og_title = soup.find('meta', property='og:novel:book_name')
    if og_title and og_title.get('content'):
        book_info['title'] = og_title['content']
    else:
        title_h2 = soup.find('h2', class_='layout-tit')
        book_info['title'] = re.sub(r'[《》]', '', title_h2.get_text(strip=True)).replace('最新章节', '').replace('正文', '') if title_h2 else '未知书籍'
    
    og_author = soup.find('meta', property='og:novel:author')
    if og_author and og_author.get('content'):
        book_info['author'] = og_author['content']
    else:
        book_info['author'] = '未知作者'
    
    og_category = soup.find('meta', property='og:novel:category')
    if og_category and og_category.get('content'):
        book_info['category'] = og_category['content']
    else:
        book_info['category'] = '未知分类'
    
    og_status = soup.find('meta', property='og:novel:status')
    if og_status and og_status.get('content'):
        book_info['status'] = og_status['content']
    else:
        book_info['status'] = '未知状态'
    
    og_update_time = soup.find('meta', property='og:novel:update_time')
    if og_update_time and og_update_time.get('content'):
        book_info['update_time'] = og_update_time['content']
    else:
        book_info['update_time'] = '未知更新时间'
    
    og_lastest_chapter = soup.find('meta', property='og:novel:lastest_chapter_name')
    if og_lastest_chapter and og_lastest_chapter.get('content'):
        book_info['lastest_chapter'] = og_lastest_chapter['content']
    else:
        book_info['lastest_chapter'] = '无最新章节信息'
    
    og_description = soup.find('meta', property='og:description')
    if og_description and og_description.get('content'):
        book_info['description'] = og_description['content']
    else:
        book_info['description'] = '暂无书籍简介'
    
    og_image = soup.find('meta', property='og:image')
    if og_image and og_image.get('content'):
        book_info['cover'] = og_image['content']
    else:
        book_info['cover'] = ''
    
    # 获取章节列表
    chapters = []
    section_boxes = soup.find_all('div', class_='section-box')
    
    for box in section_boxes:
        ul_list = box.find('ul', class_='section-list')
        if ul_list:
            for li in ul_list.find_all('li'):
                a_tag = li.find('a')
                if a_tag:
                    chapter_url = a_tag['href']
                    # 处理章节链接的相对路径
                    if chapter_url.startswith('/'):
                        full_chapter_url = site_base_url + chapter_url
                    elif not chapter_url.startswith('http'):
                        full_chapter_url = site_base_url + '/' + chapter_url
                    else:
                        full_chapter_url = chapter_url
                    
                    chapter = {
                        'title': a_tag.get_text(strip=True),
                        'url': full_chapter_url
                    }
                    chapters.append(chapter)
    
    # 获取分页信息
    pagination = []
    index_container = soup.find('div', class_='index-container')
    if index_container:
        select = index_container.find('select', id='indexselect')
        if select:
            for option in select.find_all('option'):
                option_value = option['value']
                # 处理分页链接的相对路径
                if option_value.startswith('/'):
                    full_option_url = site_base_url + option_value
                elif not option_value.startswith('http'):
                    full_option_url = site_base_url + '/' + option_value
                else:
                    full_option_url = option_value
                
                pagination.append({
                    'text': option.get_text(strip=True),
                    'value': full_option_url,
                    'selected': 'selected' in option.attrs
                })
    
    return render_template('book_detail.html', 
                         book_info=book_info,
                         chapters=chapters,
                         pagination=pagination,
                         book_url=full_book_url)

@app.route('/chapter')
def chapter():
    """
    章节阅读页
    """
    chapter_url = request.args.get('url', '')
    if not chapter_url:
        return "无效的章节链接"
    
    # 判断章节来源网站，使用对应的BASE_URL
    if "lkyuedu.com" in chapter_url:
        site_base_url = "https://www.lkyuedu.com"
    else:
        site_base_url = BASE_URL
    
    # 处理相对路径URL
    if chapter_url.startswith('/'):
        full_url = site_base_url + chapter_url
    elif not chapter_url.startswith('http'):
        full_url = site_base_url + '/' + chapter_url
    else:
        full_url = chapter_url
    
    # 使用线程池执行耗时操作
    future = executor.submit(fetch_page, full_url)
    html_content = future.result()
    
    if not html_content:
        return "无法获取章节内容"
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 获取章节标题
    title = soup.find('h1', class_='title')
    chapter_title = title.get_text(strip=True) if title else '未知章节'
    
    # 获取章节内容
    content_div = soup.find('div', id='content')
    content = ''
    if content_div:
        # 提取所有<p>标签的内容
        paragraphs = content_div.find_all('p')
        content = '\n'.join([p.get_text() for p in paragraphs])
        
        # 如果没有<p>标签，则获取所有文本
        if not content:
            content = content_div.get_text(separator='\n', strip=True)
    
    # 获取导航链接
    prev_url = None
    next_url = None
    info_url = None
    
    prev_link = soup.find('a', id='prev_url')
    next_link = soup.find('a', id='next_url')
    info_link = soup.find('a', id='info_url')
    
    # 处理导航链接的相对路径
    if prev_link and 'href' in prev_link.attrs:
        prev_href = prev_link['href']
        if prev_href.startswith('/'):
            prev_url = site_base_url + prev_href
        elif not prev_href.startswith('http'):
            prev_url = site_base_url + '/' + prev_href
        else:
            prev_url = prev_href
    
    if next_link and 'href' in next_link.attrs:
        next_href = next_link['href']
        if next_href.startswith('/'):
            next_url = site_base_url + next_href
        elif not next_href.startswith('http'):
            next_url = site_base_url + '/' + next_href
        else:
            next_url = next_href
    
    if info_link and 'href' in info_link.attrs:
        info_href = info_link['href']
        if info_href.startswith('/'):
            info_url = site_base_url + info_href
        elif not info_href.startswith('http'):
            info_url = site_base_url + '/' + info_href
        else:
            info_url = info_href
    
    return render_template('chapter.html',
                         chapter_title=chapter_title,
                         content=content,
                         prev_url=prev_url,
                         next_url=next_url,
                         info_url=info_url)

@app.route('/clear_cache')
def clear_cache_route():
    """清空缓存路由"""
    clear_cache()
    return jsonify({'status': 'success', 'message': '服务端缓存已清空'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')