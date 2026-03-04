import { defineConfig } from "hardhat/config";
import hardhatEthers from "@nomicfoundation/hardhat-ethers";

/** @type import('hardhat/config').HardhatUserConfig */
export default defineConfig({
    plugins: [hardhatEthers],
    solidity: "0.8.20",
    paths: {
        sources: "./contracts",
        tests: "./test",
        cache: "./cache",
        artifacts: "./artifacts"
    },
});
