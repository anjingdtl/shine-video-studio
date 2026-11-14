// Vite 把样式文件按原文导入（?raw）：测试要断言样式类确实存在。
// 仓库没有 @types/node，这里给这条导入补一个最小的类型声明。
declare module '*.css?raw' {
  const content: string;
  export default content;
}
