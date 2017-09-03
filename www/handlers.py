#URL处理

import re, time, json, logging, hashlib, base64, asyncio, math
import markdown2 
from aiohttp import web
from coroweb import get, post
from apis import APIValueError, APIResourceNotFoundError, Page
from models import User, Comment, Blog, next_id
from config import configs

#--------------------- ↓ 几个定义 ↓ ---------------------#

#定义cookie
COOKIE_NAME = 'session'
_COOKIE_KEY = configs.session.secret

#验证管理员
def check_admin(request):
    if request.__user__ is None or request.__user__.admin == 0:
        raise APIPermissionError()

#日志页数
def get_page_index(page_str):
    p = 1
    try:
        p = int(page_str)
    except ValueError as e:
        pass
    if p < 1:
        p = 1
    return p

#内容页翻页
def text2html(text):
    lines = map(lambda s: '<p>%s</p>' % s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), filter(lambda s: s.strip() != '', text.split('\n')))
    return ''.join(lines)

#确定邮箱
_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-f]{40}$')


#--------------------- ↑ 几个定义 ↑ ---------------------#

#--------------------- ↓ cookie ↓ ---------------------#

#创建加密cookie

def user2cookie(user, max_age):
    '''
    Generate cookie str by user.
    '''
    # build cookie string by: id-expires-sha1
    expires = str(int(time.time() + max_age))
    s = '%s-%s-%s-%s' % (user.id, user.passwd, expires, _COOKIE_KEY)
    L = [user.id, expires, hashlib.sha1(s.encode('utf-8')).hexdigest()]
    return '-'.join(L)

#解析cookie

@asyncio.coroutine
def cookie2user(cookie_str):
    '''
    Parse cookie and load user if cookie is valid.
    '''
    if not cookie_str:
        return None
    try:
        L = cookie_str.split('-')
        if len(L) != 3:
            return None
        uid, expires, sha1 = L
        if int(expires) < time.time():
            return None
        user = yield from User.find(uid)
        if user is None:
            return None
        s = '%s-%s-%s-%s' % (uid, user.passwd, expires, _COOKIE_KEY)
        if sha1 != hashlib.sha1(s.encode('utf-8')).hexdigest():
            logging.info('invalid sha1')
            return None
        user.passwd = '******'
        return user
    except Exception as e:
        logging.exception(e)
        return None

#--------------------- ↑ cookie ↑ ---------------------#


#--------------------- ↓ API模块 ↓ ---------------------#

#登录验证
@post('/api/authenticate')
def authenticate(*, email, passwd):
    if not email:
        raise APIValueError('email', 'Invalid email.')
    if not passwd:
        raise APIValueError('passwd', 'Invalid password.')
    users = yield from User.findAll('email=?', [email])
    if len(users) == 0:
        raise APIValueError('email', 'Email not exist.')
    user = users[0]
    # check passwd:
    sha1 = hashlib.sha1()
    sha1.update(user.id.encode('utf-8'))
    sha1.update(b':')
    sha1.update(passwd.encode('utf-8'))
    if user.passwd != sha1.hexdigest():
        raise APIValueError('passwd', 'Invalid password.')
    # authenticate ok, set cookie:
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r

#注册验证
@post('/api/users')
def api_register_user(*, email, name, passwd):
    if not name or not name.strip():
        raise APIValueError('name')
    if not email or not _RE_EMAIL.match(email):
        raise APIValueError('email')
    if not passwd or not _RE_SHA1.match(passwd):
        raise APIValueError('passwd')
    users = yield from User.findAll('email=?', [email])
    if len(users) > 0:
        raise APIError('register:failed', 'email', 'Email is already in use.')
    uid = next_id()
    sha1_passwd = '%s:%s' % (uid, passwd)
    user = User(id=uid, name=name.strip(), email=email, passwd=hashlib.sha1(sha1_passwd.encode('utf-8')).hexdigest(), image='about:blank')
    yield from user.save()
    # make session cookie:
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '******'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r

#创建日志
@post('/api/blogs')
def api_create_blog(request, *, name, summary, content):
    check_admin(request)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog = Blog(user_id=request.__user__.id, user_name=request.__user__.name, user_image=request.__user__.image, name=name.strip(), summary=summary.strip(), content=content.strip())
    yield from blog.save()
    return blog

#修改日志
@post('/api/blogs/{id}')
def api_update_blog(id, request, *, name, summary, content):
    check_admin(request)
    blog = yield from Blog.find(id)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog.name = name.strip()
    blog.summary = summary.strip()
    blog.content = content.strip()
    yield from blog.update()
    return blog

#删除日志
@post('/api/blogs/{id}/delete')
def api_delete_blog(request, *, id):
    check_admin(request)
    blog = yield from Blog.find(id)
    yield from blog.remove()
    return dict(id=id)


#获取日志
@get('/api/blogs')
def api_blogs(*, page='1'):
    page_index = get_page_index(page)
    num = yield from Blog.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page=p, blogs=())
    blogs = yield from Blog.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, blogs=blogs)

#修改日志
@get('/api/blogs/{id}')
def api_get_blog(*, id):
    blog = yield from Blog.find(id)
    return blog

#获取评论
@get('/api/comments')
def api_comments(*, page='1'):
    page_index = get_page_index(page)
    num = yield from Comment.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page=p, comments=())
    comments = yield from Comment.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    return dict(page=p, comments=comments)

#创建评论
@post('/api/blogs/{id}/comments')
def api_create_comment(id, request, *, content):
    user = request.__user__
    if user is None:
        raise APIPermissionError('Please signin first.')
    if not content or not content.strip():
        raise APIValueError('content')
    blog = yield from Blog.find(id)
    if blog is None:
        raise APIResourceNotFoundError('Blog')
    comment = Comment(blog_id=blog.id, blog_name=blog.name ,user_id=user.id, user_name=user.name, user_image=user.image, content=content.strip())
    yield from comment.save()
    return comment

#删除评论
@post('/api/comments/{id}/delete')
def api_delete_comments(id, request):
    check_admin(request)
    c = yield from Comment.find(id)
    if c is None:
        raise APIResourceNotFoundError('Comment')
    yield from c.remove()
    return dict(id=id)

#获取用户
@get('/api/users')
def api_get_users(*, page='1'):
    page_index = get_page_index(page)
    num = yield from User.findNumber('count(id)')
    p = Page(num, page_index)
    if num == 0:
        return dict(page=p, users=())
    users = yield from User.findAll(orderBy='created_at desc', limit=(p.offset, p.limit))
    for u in users:
        u.passwd = '******'
    return dict(page=p, users=users)

#删除用户
@post('/api/users/{id}/delete')
def api_delete_users(id, request):
    check_admin(request)
    c = yield from User.find(id)
    if c is None:
        raise APIResourceNotFoundError('User')
    yield from c.remove()
    return dict(id=id)

#用户升级
@post('/api/users/{id}/raise')
def api_raise_users(id, request):
    check_admin(request)
    c = yield from User.find(id)
    if c is None:
        raise APIResourceNotFoundError('User')
    yield from c.raiseup()
    return dict(id=id)

#用户降级
@post('/api/users/{id}/lower')
def api_lower_users(id, request):
    check_admin(request)
    c = yield from User.find(id)
    if c is None:
        raise APIResourceNotFoundError('User')
    yield from c.lower()
    return dict(id=id)

#--------------------- ↑ API模块 ↑ ---------------------#


#--------------------- ↓ 管理模块 ↓ ---------------------#

#默认管理页
@get('/manage/')
def manage():
    return 'redirect:/manage/blogs'

#创建日志
@get('/manage/create')
def manage_create_blog(*,request):
    return {
        '__template__': 'manage_blog_edit.html',
        'id': '',
        'action': '/api/blogs',
        'user': request.__user__ ,
    }

#日志列表 *默认管理页*
@get('/manage/blogs')
def manage_blogs(*, page='1',request):
    page_index = get_page_index(page)
    num = yield from Blog.findNumber('count(id)')
    p = Page(num ,page_index)
    index_num = math.ceil(num/6)
    return {
        '__template__': 'manage_blogs.html',
        'page_index': get_page_index(page) ,
        'user': request.__user__ ,
        'page': p ,
        'index_num': index_num
    }

#评论列表
@get('/manage/comments')
def manage_comments(*, page='1',request):
    page_index = get_page_index(page)
    num = yield from Comment.findNumber('count(id)')
    p = Page(num ,page_index)
    index_num = math.ceil(num/6)
    return {
        '__template__': 'manage_comments.html',
        'page_index': get_page_index(page) ,
        'user': request.__user__ ,
        'page': p ,
        'index_num': index_num

    }


#修改日志
@get('/edit/{id}')
def manage_edit_blog(*, id ,request):
    return {
        '__template__': 'manage_blog_edit.html',
        'id': id,
        'action': '/api/blogs/%s' % id ,
        'user': request.__user__ ,
    }

#用户列表
@get('/manage/users')
def manage_users(*, page='1' ,request):

    page_index = get_page_index(page)
    num = yield from User.findNumber('count(id)')
    p = Page(num ,page_index)
    index_num = math.ceil(num/6)
    return {
        '__template__': 'manage_users.html',
        'page_index': get_page_index(page),
        'user': request.__user__ ,
        'page': p ,
        'index_num': index_num
    }


#--------------------- ↑ 管理模块 ↑ ---------------------#


#--------------------- ↓ 用户模块 ↓ ---------------------#

#首页
@get('/')
def index(*, page='1',request):
    page_index = get_page_index(page)
    num = yield from Blog.findNumber('count(id)')
    page = Page(num ,page_index)
    index_num = math.ceil(num/6)
    if num == 0:
        blogs = []
        index_num = 1
    else:
        blogs = yield from Blog.findAll(orderBy='created_at desc', limit=(page.offset, page.limit))
    return {
        '__template__': 'blogs.html',
        'page': page ,
        'blogs': blogs ,
        'user': request.__user__ ,
        'index_num': index_num
    }

#注册页
@get('/register')
def register():
    return {
        '__template__': 'register.html'
    }

#登录页
@get('/signin')
def signin():
    return {
        '__template__': 'signin.html'
    }

#登出页
@get('/signout')
def signout(request):
    referer = request.headers.get('Referer')
    r = web.HTTPFound(referer or '/')
    r.set_cookie(COOKIE_NAME, '-deleted-', max_age=0, httponly=True)
    logging.info('user signed out.')
    return r


#日志详情页
@get('/blog/{id}')
def get_blog(id,*,request):
    blog = yield from Blog.find(id)
    comments = yield from Comment.findAll('blog_id=?', [id], orderBy='created_at desc')
    for c in comments:
        c.html_content = text2html(c.content)
    blog.html_content = markdown2.markdown(blog.content)
    return {
        '__template__': 'blog.html',
        'blog': blog,
        'comments': comments ,
        'user': request.__user__ ,
    }



#--------------------- ↑ 用户模块 ↑ ---------------------#