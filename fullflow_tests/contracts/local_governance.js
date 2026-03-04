import { network } from "hardhat";

async function main() {
  const { ethers } = await network.connect();
  const [governor, reporter, target] = await ethers.getSigners();

  const ContractFactory = await ethers.getContractFactory("AgentRegistry_v1");
  const contract = await ContractFactory.connect(governor).deploy(governor.address);
  await contract.waitForDeployment();

  const targetDid = `did:ethr:sepolia:${target.address}`;
  const targetCid = "local-fullflow-governance-cid";

  const registerTx = await contract
    .connect(target)
    .registerAgent(targetDid, targetCid, { value: ethers.parseEther("0.05") });
  await registerTx.wait();

  const reportTx = await contract
    .connect(reporter)
    .reportMisbehavior(target.address, "local-evidence-cid");
  await reportTx.wait();

  const slashTx = await contract
    .connect(governor)
    .slash(target.address, 20, ethers.parseEther("0.01"), "fullflow local slash");
  await slashTx.wait();

  const afterSlash = await contract.getAgent(target.address);
  const currentBlock = await ethers.provider.getBlock("latest");
  const nowTs = Number(currentBlock.timestamp);

  const restoreTx = await contract
    .connect(governor)
    .restore(target.address, 5, nowTs - 30, "fullflow local restore");
  await restoreTx.wait();

  const afterRestore = await contract.getAgent(target.address);

  const payload = {
    contractAddress: await contract.getAddress(),
    target: target.address,
    did: targetDid,
    reportSubmitted: true,
    afterSlash: {
      isSlashed: afterSlash.isSlashed,
      accumulatedPenalty: afterSlash.accumulatedPenalty.toString(),
      stakeAmount: afterSlash.stakeAmount.toString(),
    },
    afterRestore: {
      isSlashed: afterRestore.isSlashed,
      accumulatedPenalty: afterRestore.accumulatedPenalty.toString(),
      stakeAmount: afterRestore.stakeAmount.toString(),
    },
  };

  console.log(`FULLFLOW_LOCAL_GOV_RESULT=${JSON.stringify(payload)}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
