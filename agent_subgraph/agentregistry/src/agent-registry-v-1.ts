import {
  AgentAppealed as AgentAppealedEvent,
  AgentRegistered as AgentRegisteredEvent,
  AgentRestored as AgentRestoredEvent,
  AgentSlashed as AgentSlashedEvent,
  AgentUnregistered as AgentUnregisteredEvent,
  MisbehaviorReported as MisbehaviorReportedEvent,
  RoleAdminChanged as RoleAdminChangedEvent,
  RoleGranted as RoleGrantedEvent,
  RoleRevoked as RoleRevokedEvent,
  ServiceUpdated as ServiceUpdatedEvent,
  StakeUpdated as StakeUpdatedEvent,
  TreasuryUpdated as TreasuryUpdatedEvent
} from "../generated/AgentRegistry_v1/AgentRegistry_v1"
import {
  AgentAppealed,
  AgentRegistered,
  AgentRestored,
  AgentSlashed,
  AgentUnregistered,
  MisbehaviorReported,
  RoleAdminChanged,
  RoleGranted,
  RoleRevoked,
  ServiceUpdated,
  StakeUpdated,
  TreasuryUpdated
} from "../generated/schema"

export function handleAgentAppealed(event: AgentAppealedEvent): void {
  let entity = new AgentAppealed(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.evidenceCid = event.params.evidenceCid
  entity.timestamp = event.params.timestamp

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleAgentRegistered(event: AgentRegisteredEvent): void {
  let entity = new AgentRegistered(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.did = event.params.did
  entity.cid = event.params.cid
  entity.initScore = event.params.initScore
  entity.stakeAmount = event.params.stakeAmount

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleAgentRestored(event: AgentRestoredEvent): void {
  let entity = new AgentRestored(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.slashed = event.params.slashed
  entity.newTotalPenalty = event.params.newTotalPenalty
  entity.newlastMisconductTimestamp = event.params.newlastMisconductTimestamp
  entity.reason = event.params.reason

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleAgentSlashed(event: AgentSlashedEvent): void {
  let entity = new AgentSlashed(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.slashed = event.params.slashed
  entity.penaltyScore = event.params.penaltyScore
  entity.newTotalPenalty = event.params.newTotalPenalty
  entity.slashedEthAmount = event.params.slashedEthAmount
  entity.reason = event.params.reason

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleAgentUnregistered(event: AgentUnregisteredEvent): void {
  let entity = new AgentUnregistered(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.did = event.params.did

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleMisbehaviorReported(
  event: MisbehaviorReportedEvent
): void {
  let entity = new MisbehaviorReported(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.reporter = event.params.reporter
  entity.targetAgent = event.params.targetAgent
  entity.evidenceCid = event.params.evidenceCid
  entity.timestamp = event.params.timestamp

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleRoleAdminChanged(event: RoleAdminChangedEvent): void {
  let entity = new RoleAdminChanged(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.role = event.params.role
  entity.previousAdminRole = event.params.previousAdminRole
  entity.newAdminRole = event.params.newAdminRole

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleRoleGranted(event: RoleGrantedEvent): void {
  let entity = new RoleGranted(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.role = event.params.role
  entity.account = event.params.account
  entity.sender = event.params.sender

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleRoleRevoked(event: RoleRevokedEvent): void {
  let entity = new RoleRevoked(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.role = event.params.role
  entity.account = event.params.account
  entity.sender = event.params.sender

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleServiceUpdated(event: ServiceUpdatedEvent): void {
  let entity = new ServiceUpdated(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.newCid = event.params.newCid

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleStakeUpdated(event: StakeUpdatedEvent): void {
  let entity = new StakeUpdated(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.agentAddress = event.params.agentAddress
  entity.oldStake = event.params.oldStake
  entity.newStake = event.params.newStake
  entity.oldInitScore = event.params.oldInitScore
  entity.newInitScore = event.params.newInitScore

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

export function handleTreasuryUpdated(event: TreasuryUpdatedEvent): void {
  let entity = new TreasuryUpdated(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.oldTreasury = event.params.oldTreasury
  entity.newTreasury = event.params.newTreasury

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}
