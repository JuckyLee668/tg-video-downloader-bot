/**
 * API 请求队列管理器
 * 用于限制同时进行的 API 请求数量，避免服务器压力过大
 */
export class ApiRequestQueue {
  constructor(logger, maxConcurrent = 3) {
    this.logger = logger;
    this.maxConcurrent = maxConcurrent; // 最大并发请求数
    this.activeRequests = new Set(); // 当前活跃的请求
    this.requestQueue = []; // 等待队列
  }

  /**
   * 添加请求到队列
   * @param {Function} requestFn - 返回 Promise 的请求函数
   * @param {string} requestId - 请求标识（用于日志）
   * @returns {Promise} 请求结果
   */
  async addRequest(requestFn, requestId = 'unknown') {
    return new Promise((resolve, reject) => {
      const executeRequest = async () => {
        // 添加到活跃请求
        this.activeRequests.add(requestId);
        
        try {
          const result = await requestFn();
          resolve(result);
        } catch (error) {
          reject(error);
        } finally {
          // 从活跃请求中移除
          this.activeRequests.delete(requestId);
          
          // 处理队列中的下一个请求
          this.processQueue();
        }
      };

      // 如果当前活跃请求数小于最大并发数，立即执行
      if (this.activeRequests.size < this.maxConcurrent) {
        this.logger.debug(`API 请求立即执行: ${requestId} (活跃: ${this.activeRequests.size + 1}/${this.maxConcurrent})`);
        executeRequest();
      } else {
        // 否则加入等待队列
        this.requestQueue.push({ executeRequest, requestId });
        this.logger.info(`API 请求加入队列: ${requestId.substring(0, 30)}... (队列长度: ${this.requestQueue.length}, 活跃: ${this.activeRequests.size}/${this.maxConcurrent})`);
      }
    });
  }

  /**
   * 处理队列中的请求
   */
  processQueue() {
    while (
      this.requestQueue.length > 0 &&
      this.activeRequests.size < this.maxConcurrent
    ) {
      const { executeRequest, requestId } = this.requestQueue.shift();
      this.logger.info(`从队列中取出请求: ${requestId.substring(0, 30)}... (队列剩余: ${this.requestQueue.length}, 活跃: ${this.activeRequests.size + 1}/${this.maxConcurrent})`);
      executeRequest();
    }
  }

  /**
   * 获取当前状态
   */
  getStatus() {
    return {
      active: this.activeRequests.size,
      queued: this.requestQueue.length,
      maxConcurrent: this.maxConcurrent
    };
  }

  /**
   * 设置最大并发数
   */
  setMaxConcurrent(maxConcurrent) {
    this.maxConcurrent = maxConcurrent;
    // 如果新的并发数更大，处理更多队列中的请求
    this.processQueue();
  }
}
