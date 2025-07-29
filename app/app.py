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
    page_url = request.args.get('page_url', '')  # 支持直接指定分页URL
    source = request.args.get('source', '')  # 书籍来源
    custom_url = request.args.get('custom_url', '')  # 自定义基础URL
    
    if not book_url and not page_url:
        return "无效的书籍链接"
    
    # 确定使用的基础URL
    if custom_url:
        site_base_url = custom_url
    elif "lkyuedu.com" in (book_url or page_url):
        site_base_url = "https://www.lkyuedu.com"
    else:
        site_base_url = BASE_URL
    
    # 确定实际请求的URL
    if page_url:
        # 如果提供了page_url，直接使用
        full_book_url = page_url
    else:
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
    
    # 查找所有章节列表容器，但排除"最新章节"部分
    section_boxes = soup.find_all('div', class_='section-box')
    
    # 过滤掉"最新章节"部分，只保留正文部分
    filtered_boxes = []
    for box in section_boxes:
        # 查找父容器中的标题
        parent = box.parent
        layout_tit = parent.find('h2', class_='layout-tit') if parent else None
        if layout_tit and '最新章节' in layout_tit.get_text():
            # 跳过"最新章节"部分
            continue
        else:
            # 保留其他部分（如正文）
            filtered_boxes.append(box)
    
    # 如果没有找到非最新章节的section-box，回退到原来的逻辑
    if not filtered_boxes and section_boxes:
        filtered_boxes = section_boxes
    
    # 如果仍然没有找到标准的section-box，尝试查找其他可能的章节容器
    if not filtered_boxes:
        # 查找所有ul标签中class包含section-list的
        section_lists = soup.find_all('ul', class_=re.compile(r'section-list'))
        if section_lists:
            # 过滤掉"最新章节"部分的列表
            filtered_lists = []
            for ul in section_lists:
                # 查找父容器中的标题
                parent_row = None
                current = ul
                # 向上查找包含标题的父容器
                while current and current.parent:
                    if current.parent.find('h2', class_='layout-tit'):
                        parent_row = current.parent
                        break
                    current = current.parent
                
                layout_tit = parent_row.find('h2', class_='layout-tit') if parent_row else None
                if layout_tit and '最新章节' in layout_tit.get_text():
                    # 跳过"最新章节"部分
                    continue
                # 新增过滤条件：排除包含"如来大世尊"等异常章节的列表
                if ul.find('a', string=re.compile(r'如来大世尊|异世佛门|佛国|公孙轩辕')):
                    continue
                
                filtered_lists.append(ul)
            
            # 如果过滤后没有列表，但原始列表存在，则回退到原始列表
            if not filtered_lists and section_lists:
                filtered_lists = section_lists
                
            filtered_boxes = [{'ul': ul} for ul in filtered_lists]
    
    for box in filtered_boxes:
        # 如果是div.section-box
        if hasattr(box, 'find'):
            ul_list = box.find('ul', class_='section-list')
        # 如果是模拟的字典结构
        else:
            ul_list = box.get('ul')
            
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
    # 查找分页容器，支持多种class
    index_containers = soup.find_all('div', class_=re.compile(r'index-container'))
    
    for index_container in index_containers:
        select = index_container.find('select', id=re.compile(r'indexselect'))
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
                
                # 构建内部路由URL，而不是直接使用外部网站URL
                from urllib.parse import urlencode
                internal_params = {
                    'page_url': full_option_url,
                    'source': source if source else ('lkyuedu' if 'lkyuedu.com' in full_option_url else 'kanshulao')
                }
                if custom_url:
                    internal_params['custom_url'] = custom_url
                
                # 构建内部路由URL
                internal_url = '/book?' + urlencode(internal_params)
                
                pagination.append({
                    'text': option.get_text(strip=True),
                    'value': internal_url,  # 使用内部路由URL
                    'selected': 'selected' in option.attrs
                })
            break  # 只处理第一个找到的分页控件
    
    # 如果没有找到select分页，检查是否有其他分页链接
    if not pagination:
        # 查找其他可能的分页元素
        pagination_div = soup.find('div', class_=re.compile(r'pagination|pages'))
        if pagination_div:
            links = pagination_div.find_all('a')
            for link in links:
                href = link.get('href')
                text = link.get_text(strip=True)
                if href and text:
                    # 处理分页链接的相对路径
                    if href.startswith('/'):
                        full_href = site_base_url + href
                    elif not href.startswith('http'):
                        full_href = site_base_url + '/' + href
                    else:
                        full_href = href
                    
                    # 构建内部路由URL
                    internal_params = {
                        'page_url': full_href,
                        'source': source if source else ('lkyuedu' if 'lkyuedu.com' in full_href else 'kanshulao')
                    }
                    if custom_url:
                        internal_params['custom_url'] = custom_url
                    
                    # 构建内部路由URL
                    internal_url = '/book?' + urlencode(internal_params)
                    
                    pagination.append({
                        'text': text,
                        'value': internal_url,  # 使用内部路由URL
                        'selected': 'class' in link.attrs and 'current' in link['class']
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
    book_url = request.args.get('book_url', '')  # 获取书籍URL参数
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
                         info_url=info_url,
                         book_url=book_url)  # 传递书籍URL参数到模板

@app.route('/clear_cache')
def clear_cache_route():
    """清空缓存路由"""
    clear_cache()
    return jsonify({'status': 'success', 'message': '服务端缓存已清空'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')