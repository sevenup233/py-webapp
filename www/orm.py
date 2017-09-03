#ORM（Object-Relational Mapping）
#基于协程封装MySQL语句

import asyncio,aiomysql,logging

#日志：数据库名
def log(sql,args=()):
    logging.info("SQL: %s"%(sql))

#创建连接池
@asyncio.coroutine
def create_pool(loop, **kw):
    #日志：创建数据库
    logging.info('create database connection pool...')
    #定义全局变量__pool，作为连接池
    global __pool
    __pool = yield from aiomysql.create_pool(
        #定义连接池参数基本属性
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        #db:database 
        db=kw['db'],
        charset=kw.get('charset', 'utf8'),
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
    )

#销毁连接池
@asyncio.coroutine
def destory_pool(): 
    global __pool
    if __pool is not None:
        __pool.close()
        yield from  __pool.wait_closed()

#Select
@asyncio.coroutine
def select(sql, args, size=None):
    log(sql,args)
    global __pool
    #打开pool
    with (yield from __pool) as conn:
        #定义游标cur
        cur = yield from conn.cursor(aiomysql.DictCursor)
        yield from cur.execute(sql.replace('?', '%s'), args or ())
        if size:
            rs = yield from cur.fetchmany(size)
        else:
            rs = yield from cur.fetchall()
        #关闭游标
        yield from cur.close()
        logging.info('rows returned: %s' % len(rs))
        #rs为cur所指的对象
        return rs

#Execute(Insert, Update, Delete)
@asyncio.coroutine
def execute(sql, args):
    log(sql)
    #打开pool
    with (yield from __pool) as conn:
        try:
            cur = yield from conn.cursor()
            yield from cur.execute(sql.replace('?', '%s'), args)
            affected = cur.rowcount
            yield from cur.close()
        except BaseException as e:
            raise
        finally:
            conn.close()
        return affected

#定义Field类来保存如Users类在数据库中每一列的属性
class Field(object):

    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)

#定义数据类型

#字符串
class StringField(Field):

    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)

#是否
class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)

#常数
class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

#浮点数
class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

#文本
class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)

#用于输出元类中创建sql_insert语句中的占位符
def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)

#定义Modle的元类
class ModelMetaclass(type):
    #调用__init__方法前会调用__new__方法
    #参数分别为：当前准备创建的类的对象，类的名字，类继承的父类集合，类的方法集合
    def __new__(cls, name, bases, attrs):
        #对Model类的实例执行此函数
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        #如果没设置__table__属性，tablename就是类的名字
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s (table: %s)' % (name, tableName))
        #mappings保存attrs的映射关系
        mappings = {}
        #fields保存主键以外的属性
        fields = []
        primarykey = None
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('found mapping: %s ==> %s' % (k, v))
                #把键值k，v对存入字典mapping中
                mappings[k] = v
                #找到主键
                if v.primary_key:
                    if primarykey:
                        raise Exception('Duplicate primary key for field: %s' % k)
                    primarykey = k
                else:
                    fields.append(k)
        if not primarykey:
            raise Exception('Primary key not found.')
        # 删除类属性
        for k in mappings.keys():
            attrs.pop(k)
        # 保存除主键外的属性名为``（运算出字符串）列表形式
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))
        attrs['__mappings__'] = mappings  # 保存属性和列的映射关系
        attrs['__table__'] = tableName
        attrs['__primary_key__'] = primarykey  # 主键属性名
        attrs['__fields__'] = fields  # 除主键外的属性名
        # 反引号和repr()函数功能一致
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primarykey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (
        tableName, ', '.join(escaped_fields), primarykey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (
        tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primarykey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primarykey)
        attrs['__raise__'] = 'update users set admin = 1 where `%s`=?' % (primarykey)
        attrs['__lower__'] = 'update users set admin = 0 where `%s`=?' % (primarykey)
        return type.__new__(cls, name, bases, attrs)

#定义Modle类，从ModelMetaclass继承
class Model(dict, metaclass=ModelMetaclass):

    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, str(value)))
                setattr(self, key, value)
        return value

#定义findAll功能，从数据库中获取全部该类的数据
    @classmethod
    @asyncio.coroutine 
    def findAll(cls, where=None, args=None, **kw):
        ' find objects by where clause. '
        sql = [cls.__select__]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        rs = yield from select(' '.join(sql), args)
        return [cls(**r) for r in rs]

#定义findNumber功能，根据ID从数据库中获取数据
    @classmethod
    @asyncio.coroutine 
    def findNumber(cls, selectField, where=None, args=None):
        ' find number by select and where. '
        sql = ['select %s _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        rs = yield from select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        return rs[0]['_num_']

    @classmethod
    @asyncio.coroutine
    def find(cls, pk):
        ' find object by primary key. '
        rs = yield from select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

#保存数据
    @asyncio.coroutine
    def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows = yield from execute(self.__insert__, args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

#更新数据
    @asyncio.coroutine
    def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = yield from execute(self.__update__, args)
        if rows != 1:
            logging.warn('failed to update by primary key: affected rows: %s' % rows)

#删除数据
    @asyncio.coroutine
    def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = yield from execute(self.__delete__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)

#用户升级
    @asyncio.coroutine
    def raiseup(self):
        args = [self.getValue(self.__primary_key__)]
        rows = yield from execute(self.__raise__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)

#用户降级
    @asyncio.coroutine
    def lower(self):
        args = [self.getValue(self.__primary_key__)]
        rows = yield from execute(self.__lower__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)