// 这是一个 Node.js 脚本，专门用于调用官方库解析 DID

const { Resolver } = require('did-resolver');
const { getResolver } = require('ethr-did-resolver');
const { providers } = require('ethers'); // 引入 ethers 库

// 1. 从命令行接收参数: python 会把参数传到这里
// args[0] 是 DID, args[1] 是 API URL
const args = process.argv.slice(2);
const did = args[0];
const rpcUrl = args[1];

if (!did || !rpcUrl) {
    console.error("Error: 请提供 DID 和 RPC URL");
    process.exit(1);
}

async function run() {
    try {
        // 2. 配置连接
        // 我们需要告诉解析器：'sepolia' 网络对应的 RPC 链接是什么
        const providerConfig = {
            networks: [
                {
                    name: "sepolia",
                    rpcUrl: rpcUrl,
                    chainId: 11155111,
                    // 这是官方合约地址，库里默认有，为了保险显式写上
                    registry: "0x03d5003bf0e79C5F5223588F347ebA39AfbC3818"
                }
            ]
        };

        // 3. 初始化官方解析器
        const ethrDidResolver = getResolver(providerConfig);
        const didResolver = new Resolver(ethrDidResolver);

        // 4. 执行解析 (去链上查数据)
        const doc = await didResolver.resolve(did);

        // 5. 将结果以 JSON 格式打印出来 (Python 会截获这段打印内容)
        console.log(JSON.stringify(doc, null, 4));

    } catch (error) {
        console.error("解析出错:", error);
        process.exit(1);
    }
}

run();