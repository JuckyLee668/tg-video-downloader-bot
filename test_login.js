// 测试登录功能的简单脚本
import http from 'http';

console.log('开始测试登录API...');

// 首先测试test路由
const testOptions = {
  hostname: '127.0.0.1',
  port: 3000,
  path: '/test',
  method: 'GET',
  headers: {
    'Accept': 'application/json',
  }
};

console.log('首先测试/test路由...');
const testReq = http.request(testOptions, (res) => {
  console.log(`/test 状态码: ${res.statusCode}`);
  let data = '';
  res.on('data', (chunk) => {
    data += chunk;
  });
  res.on('end', () => {
    console.log(`/test 响应体: ${data}`);
    
    // 然后测试登录API
    testLoginAPI();
  });
});

testReq.on('error', (e) => {
  console.error(`/test 请求失败: ${e.message}`);
  testLoginAPI();
});

testReq.end();

function testLoginAPI() {
  const options = {
    hostname: '127.0.0.1',
    port: 3000,
    path: '/api/login',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    }
  };

  console.log('\n发送登录请求到:', `http://${options.hostname}:${options.port}${options.path}`);

  const req = http.request(options, (res) => {
    console.log(`状态码: ${res.statusCode}`);

    let data = '';
    res.on('data', (chunk) => {
      data += chunk;
    });

    res.on('end', () => {
      console.log(`响应体: ${data}`);
      try {
        const jsonData = JSON.parse(data);
        console.log('解析后的JSON:', jsonData);
      } catch (e) {
        console.log('响应不是有效的JSON');
      }
    });
  });

  req.on('error', (e) => {
    console.error(`请求失败: ${e.message}`);
  });

  const payload = JSON.stringify({
    username: 'admin',
    password: 'admin123'
  });

  console.log('请求体:', payload);

  req.write(payload);
  req.end();
}