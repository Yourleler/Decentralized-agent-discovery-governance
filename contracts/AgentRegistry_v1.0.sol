// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import "solady/src/utils/FixedPointMathLib.sol";

/**
 * @title AgentRegistry_v1.0
 * @dev 融合版 Agent 发现与治理架构的核心注册合约 (Layer 1)
 *
 * 设计要点：
 * - 参考ERC-8004,后续可能接入
 * - 仅 Admin(经济责任主体) 在链上注册并质押
 * - 链上存 DID + metadataCid 作为信任锚点，供 The Graph/Sidecar 同步与索引
 * - 罚没资金进入 Treasury，避免 msg.sender 直接受益
 */
contract AgentRegistry_v1 is AccessControl, ReentrancyGuard {
    using FixedPointMathLib for uint256;

    // ----------------------------
    // Roles
    // ----------------------------
    bytes32 public constant GOVERNANCE_ROLE = keccak256("GOVERNANCE_ROLE");

    // ----------------------------
    // Parameters  稳定性优先,因此是常量
    // ----------------------------
    uint256 public constant SCORE_BASE = 60;
    uint256 public constant SCORE_MAX = 100;
    uint256 public constant SCORE_MIN = 40; //低于此分数则被视为罚没死
    int256 public constant SCORE_RECOVERY_RATE = 2; //分数恢复速率
    // S_init = SCORE_BASE + FACTOR * ln(1 + STAKE_MULTIPLIER * stake)
    // 其中所有变量均按 UD60x18（1e18 精度）参与计算
    uint256 public constant FACTOR = 55e17; // 5.5 * 1e18
    uint256 public constant STAKE_MULTIPLIER = 150; // 无量纲整数，用于放大 stake 影响

    // Treasury (罚没金接收地址,质押金在本合约下,罚没转入 treasury)  其中的罚没部分可以用来激励优秀agent
    address public treasury;

    // ----------------------------
    // Data Model
    // ----------------------------
    struct Agent {
        string did; // did:ethr:...
        string metadataCid; // IPFS CID (Service Metadata)
        uint256 initScore; // S_init
        uint256 accumulatedPenalty; // P_total
        uint256 lastMisconductTimestamp; // T_last
        uint256 stakeAmount; // 当前质押（wei）
        bool isSlashed; // 是否被罚死
        //不和注销共用是因为下线不是agent主观操作,可能被错误判罚,可以申诉解除,若使用注销逻辑信息被抹除
        bool isRegistered;
        bool isfrozen; //由治理者决定是否冻结其注销动作,以防造假后在处理期间跑路
        address admin; // 经济责任主体
    }

    // admin address => Agent
    mapping(address => Agent) public agents;

    // did string => admin address
    mapping(string => address) public didToAddress;

    // ----------------------------
    // Events
    // ----------------------------

    //注册
    event AgentRegistered(
        address indexed agentAddress,
        string did,
        string cid,
        uint256 initScore,
        uint256 stakeAmount
    );

    //注销
    event AgentUnregistered(address indexed agentAddress, string did);

    //更新服务描述
    event ServiceUpdated(address indexed agentAddress, string newCid);

    //举报违规行为
    event MisbehaviorReported(
        address indexed reporter,
        address indexed targetAgent,
        string evidenceCid,
        uint256 timestamp
    );

    //申诉 agentAddress是申诉方
    event AgentAppealed(
        address indexed agentAddress,
        string evidenceCid,
        uint256 timestamp
    );

    //罚没
    event AgentSlashed(
        address indexed agentAddress,
        bool indexed slashed,
        uint256 penaltyScore,
        uint256 newTotalPenalty,
        uint256 slashedEthAmount,
        string reason
    );

    // 恢复
    event AgentRestored(
        address indexed agentAddress,
        bool indexed slashed,
        uint256 newTotalPenalty,
        uint256 newlastMisconductTimestamp,
        string reason
    );

    //更新金库
    event TreasuryUpdated(
        address indexed oldTreasury,
        address indexed newTreasury
    );

    //质押变更（增加或减少质押时触发，同时更新 initScore）
    event StakeUpdated(
        address indexed agentAddress,
        uint256 oldStake,
        uint256 newStake,
        uint256 oldInitScore,
        uint256 newInitScore
    );

    // ----------------------------
    // Constructor
    // ----------------------------
    //Governance Role 并不等价于中心化仲裁者，
    //而是一个可替换、可轮换
    //可由 DAO / 仲裁网络 / 乐观仲裁协议（Optimistic Arbitration）承担的执行接口
    // ----------------------------
    constructor(address _treasury) {
        require(_treasury != address(0), "Treasury cannot be zero");
        treasury = _treasury;

        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender); //创建者拥有默认管理员角色
        _grantRole(GOVERNANCE_ROLE, msg.sender); //创建者拥有治理角色
    }

    // ----------------------------
    // Admin Ops
    // ----------------------------

    //设置金库(只有默认管理员可以设置)
    function setTreasury(
        address _treasury
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(_treasury != address(0), "Treasury cannot be zero");
        address old = treasury;
        treasury = _treasury;
        emit TreasuryUpdated(old, _treasury);
    }

    // ----------------------------
    // Internal Logic
    // ----------------------------

    /**
     * @dev S_init = SCORE_BASE + FACTOR * ln(1 + STAKE_MULTIPLIER * stake)
     *
     * - stake 以 wei 表示，本身就是 1e18 精度
     * - 1 在 UD60x18 中表示为 1e18
     * - ln 输入必须 > 0；这里至少为 1e18
     *
     *最少约 0.00133 ETH（≈ 1.33 mETH）才能让 S_init 从 60 变成 61
     * 返回值为“整数分数”，因此最后除以 1e18 抹掉小数部分。
     */
    function _calculateInitScore(
        uint256 _stakeWei
    ) internal pure returns (uint256) {
        // x = 1 + k * stake   (WAD, 1e18)
        uint256 x = 1e18 + (_stakeWei * STAKE_MULTIPLIER);
        require(x <= uint256(type(int256).max), "stake overflow");
        // Solady 的 lnWad 接受 int256
        int256 lnX = FixedPointMathLib.lnWad(int256(x));

        // ln(x) >= 0，这里可以安全转回 uint
        uint256 lnXUint = uint256(lnX);

        uint256 additionalScore = (FACTOR * lnXUint) / 1e18;

        if (SCORE_MAX >= SCORE_BASE + additionalScore) {
            return SCORE_BASE + additionalScore;
        } else {
            return SCORE_MAX;
        }
    }

    // ----------------------------
    // View Functions
    // ----------------------------
    function getAgent(
        address _agentAddress
    ) external view returns (Agent memory) {
        return agents[_agentAddress];
    }

    /**
     * @dev T-CPRM: S_global = min(S_init, S_init - P_total + SCORE_RECOVERY_RATE * floor((now - T_last) / 1 days))
     * 计算 Agent 的当前动态全局信誉分
     */
    function getGlobalScore(
        address _agentAddress
    ) public view returns (uint256) {
        Agent memory agent = agents[_agentAddress];
        if (!agent.isRegistered) return 0;

        // 防止减法溢出：如果累计罚分已超过初始分，返回 0
        if (agent.accumulatedPenalty >= agent.initScore) return 0;

        int256 baseParams = int256(agent.initScore) -
            int256(agent.accumulatedPenalty);

        uint256 daysSinceLast = 0;
        if (agent.lastMisconductTimestamp > 0) {
            daysSinceLast =
                (block.timestamp - agent.lastMisconductTimestamp) /
                1 days;
        }

        int256 sGlobal = baseParams +
            SCORE_RECOVERY_RATE *
            int256(daysSinceLast);

        if (sGlobal > int256(agent.initScore)) return agent.initScore;
        if (sGlobal < 0) return 0;
        return uint256(sGlobal);
    }

    // ----------------------------
    // Core Functions
    // ----------------------------

    /**
     * @dev 注册新 Agent（agent Admin 注册 & 质押）
     */
    function registerAgent(
        string calldata _did,
        string calldata _cid
    ) external payable nonReentrant {
        //nonReentrant防止重入攻击
        require(!agents[msg.sender].isRegistered, "Agent already registered");
        require(
            didToAddress[_did] == address(0),
            "DID already registered or was previously registered"
        );
        require(bytes(_did).length > 0, "DID cannot be empty");
        require(bytes(_cid).length > 0, "CID cannot be empty");
        require(bytes(_did).length <= 512, "DID too long");
        require(bytes(_cid).length <= 512, "CID too long"); //防止恶意输入过长字符串

        uint256 initScore = _calculateInitScore(msg.value);

        agents[msg.sender] = Agent({
            did: _did,
            metadataCid: _cid,
            initScore: initScore,
            accumulatedPenalty: 0,
            lastMisconductTimestamp: 0,
            stakeAmount: msg.value,
            isSlashed: false,
            isRegistered: true,
            admin: msg.sender,
            isfrozen: false
        });

        didToAddress[_did] = msg.sender;

        emit AgentRegistered(msg.sender, _did, _cid, initScore, msg.value);
    }

    /**
     * @dev 注销 Agent（agent Admin 注销 & 质押）,被冻结不能注销
     */
    function unregisterAgent(string calldata _did) external nonReentrant {
        require(agents[msg.sender].isRegistered, "Agent not registered");
        require(didToAddress[_did] == msg.sender, "DID does not match");
        require(!agents[msg.sender].isfrozen, "Agent is frozen");

        uint256 stakeAmount = agents[msg.sender].stakeAmount;

        agents[msg.sender].isRegistered = false;

        //退出 ≠ 抹除历史
        (bool success, ) = payable(msg.sender).call{value: stakeAmount}("");
        require(success, "Transfer failed");
        agents[msg.sender].stakeAmount = 0;

        // agents[msg.sender].did = "";
        // agents[msg.sender].metadataCid = "";
        // agents[msg.sender].initScore = 0;
        // agents[msg.sender].accumulatedPenalty = 0;
        // agents[msg.sender].lastMisconductTimestamp = 0;
        // agents[msg.sender].admin = address(0);
        // agents[msg.sender].isfrozen = false;
        //didToAddress[_did] = address(0);
        //这里不清空，就是让：一个钱包地址，终身只能注册一次 Agent。 (再次注册时，会提示 Agent already registered or was previously registered)
        //一旦注销，该地址永久作废（在当前合约上下文中），想再次加入必须启用新的钱包地址。
        //达到防止无成本原地洗白的效果。
        emit AgentUnregistered(msg.sender, _did);
    }

    /**
     * @dev 更新服务描述 CID（发现层 Sidecar 拉取该 CID 对应的 Service Metadata）
     */
    function updateServiceMetadata(string calldata _newCid) external {
        require(agents[msg.sender].isRegistered, "Agent not registered");
        require(!agents[msg.sender].isSlashed, "Agent is slashed");
        require(bytes(_newCid).length > 0, "CID cannot be empty");
        require(bytes(_newCid).length <= 512, "CID too long"); //防止恶意输入过长字符串

        agents[msg.sender].metadataCid = _newCid;
        emit ServiceUpdated(msg.sender, _newCid);
    }

    /**
     * @dev 增加质押，同时重算 initScore
     */
    function depositStake() external payable {
        require(agents[msg.sender].isRegistered, "Agent not registered");
        require(!agents[msg.sender].isSlashed, "Agent is slashed");

        uint256 oldStake = agents[msg.sender].stakeAmount;
        uint256 oldInitScore = agents[msg.sender].initScore;

        agents[msg.sender].stakeAmount += msg.value;
        uint256 newInitScore = _calculateInitScore(
            agents[msg.sender].stakeAmount
        );
        agents[msg.sender].initScore = newInitScore;

        emit StakeUpdated(
            msg.sender,
            oldStake,
            agents[msg.sender].stakeAmount,
            oldInitScore,
            newInitScore
        );
    }

    /**
     * @dev 减少质押，同时重算 initScore
     * 注意：减持后的 sGlobal 不能低于 SCORE_MIN
     */
    function withdrawStake(uint256 _amount) external nonReentrant {
        require(agents[msg.sender].isRegistered, "Agent not registered");
        require(!agents[msg.sender].isSlashed, "Agent is slashed");
        require(
            agents[msg.sender].stakeAmount >= _amount,
            "Insufficient stake"
        );

        uint256 oldStake = agents[msg.sender].stakeAmount;
        uint256 oldInitScore = agents[msg.sender].initScore;

        // 先更新状态
        agents[msg.sender].stakeAmount -= _amount;
        agents[msg.sender].initScore = _calculateInitScore(
            agents[msg.sender].stakeAmount
        );

        // 用 getGlobalScore 检查减持后的分数，如果不通过整个交易会 revert 回滚
        require(
            getGlobalScore(msg.sender) > SCORE_MIN,
            "Withdrawal would drop score below minimum"
        );

        (bool success, ) = payable(msg.sender).call{value: _amount}("");
        require(success, "Transfer failed");

        emit StakeUpdated(
            msg.sender,
            oldStake,
            agents[msg.sender].stakeAmount,
            oldInitScore,
            agents[msg.sender].initScore
        );
    }

    /**
     * @dev 举报违规行为（链上仅存证据 CID 的索引，不做重验证）验证由治理委员会完成
     */
    function reportMisbehavior(
        address _targetAgent,
        string calldata _evidenceCid
    ) external {
        require(agents[_targetAgent].isRegistered, "Target not registered");
        require(bytes(_evidenceCid).length > 0, "Evidence CID cannot be empty");

        emit MisbehaviorReported(
            msg.sender,
            _targetAgent,
            _evidenceCid,
            block.timestamp
        );
    }

    /**
     * @dev 申诉
     * @param _evidenceCid 申诉证据CID
     */
    function appeal(string calldata _evidenceCid) external {
        require(bytes(_evidenceCid).length > 0, "Evidence CID cannot be empty");
        emit AgentAppealed(msg.sender, _evidenceCid, block.timestamp);
    }

    /**
     * @dev 治理者冻结
     */
    function freeze(address _targetAgent) external onlyRole(GOVERNANCE_ROLE) {
        require(!agents[_targetAgent].isfrozen, "Target is frozen");
        agents[_targetAgent].isfrozen = true;
    }

    /**
     * @dev 治理者解冻
     */
    function unfreeze(address _targetAgent) external onlyRole(GOVERNANCE_ROLE) {
        require(agents[_targetAgent].isfrozen, "Target is not frozen");
        agents[_targetAgent].isfrozen = false;
    }

    /**
     * @dev 治理罚没（仅 Governance 调用）
     * - 信誉扣分：accumulatedPenalty += _penaltyScore
     * - 资金罚没：从 stakeAmount 扣减并转入 treasury
     *
     * 信誉低于阈值时直接强制 isSlashed=true
     */
    function slash(
        address _targetAgent,
        uint256 _penaltyScore,
        uint256 _slashEth,
        string calldata _reason
    ) external onlyRole(GOVERNANCE_ROLE) nonReentrant {
        Agent storage agent = agents[_targetAgent];
        require(agent.isRegistered, "Agent not registered");
        // 注意：这里建议去掉 !isSlashed 检查，允许对已 Slash 的坏人继续追加罚款
        // require(!agent.isSlashed, "Agent already slashed");

        // 先获取当前的 sGlobal（罚分前）
        uint256 currentSGlobal = getGlobalScore(_targetAgent);

        // 更新累计罚分和时间戳
        agent.accumulatedPenalty += _penaltyScore;
        agent.lastMisconductTimestamp = block.timestamp;

        // --- 基于当前 sGlobal 判断是否罚没 ---
        // 如果当前 sGlobal 减去本次罚分后会低于或等于 SCORE_MIN，则置为罚没状态
        if (
            currentSGlobal <= _penaltyScore ||
            currentSGlobal - _penaltyScore <= SCORE_MIN
        ) {
            agent.isSlashed = true;
        }
        // --------------------

        if (_slashEth > 0) {
            // --- 建议优化：避免“余额不足”导致无法罚款 ---
            // 如果只有 0.5 ETH 但要罚 1 ETH，直接扣光所有，而不是报错回滚
            uint256 actualSlashAmount = _slashEth;
            if (agent.stakeAmount < _slashEth) {
                actualSlashAmount = agent.stakeAmount;
            }

            if (actualSlashAmount > 0) {
                agent.stakeAmount -= actualSlashAmount;
                (bool ok, ) = treasury.call{value: actualSlashAmount}("");
                require(ok, "Treasury transfer failed");
            }
        }

        emit AgentSlashed(
            _targetAgent,
            agent.isSlashed, // 注意：事件里最好传当前最新的状态
            _penaltyScore,
            agent.accumulatedPenalty,
            _slashEth, // 或者 actualSlashAmount
            _reason
        );
    }

    /** * @dev 治理恢复 (与 slash 对应)
     * 用于给予被罚没节点申诉，通过治理委员会决定是否将其状态从 Slashed 恢复为正常。
     * * 安全机制：
     * 1. 仅重置状态和部分信誉分。
     * 2. 不包含资金注入功能（防止凭空印钞漏洞）。
     */
    function restore(
        address _targetAgent,
        uint256 resetPenalty,
        uint256 _resetlastMisconductTimestamp,
        string calldata _reason
    ) external onlyRole(GOVERNANCE_ROLE) {
        Agent storage agent = agents[_targetAgent];
        require(agent.isRegistered, "Target not registered");
        // 防止减法溢出：确保 resetPenalty 不大于 initScore
        require(
            resetPenalty <= agent.initScore,
            "Reset penalty exceeds init score"
        );
        // 要求恢复后的分数必须大于及格线，否则刚恢复又会被 slash
        require(
            agent.initScore - resetPenalty > SCORE_MIN,
            "Score still too low"
        );
        // 防止时间悖论：不允许设置未来的时间，否则 getGlobalScore 会崩溃
        require(
            _resetlastMisconductTimestamp <= block.timestamp,
            "Time cannot be in future"
        );
        // -----------------------
        // 1. 解除死刑状态
        agent.isSlashed = false;

        // 2. 修复信誉：
        agent.accumulatedPenalty = resetPenalty;

        // 3. 重置作恶时间戳
        agent.lastMisconductTimestamp = _resetlastMisconductTimestamp;

        // 注意：资金必须由 Agent 自己调用 depositStake() 补齐，此处绝不操作资金。

        emit AgentRestored(
            _targetAgent,
            agent.isSlashed,
            agent.accumulatedPenalty,
            agent.lastMisconductTimestamp,
            _reason
        );
    }
}
