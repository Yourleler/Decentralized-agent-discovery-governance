import { newMockEvent } from "matchstick-as"
import { ethereum, Address, BigInt, Bytes } from "@graphprotocol/graph-ts"
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
} from "../generated/AgentRegistry_v1/AgentRegistry_v1"

export function createAgentAppealedEvent(
  agentAddress: Address,
  evidenceCid: string,
  timestamp: BigInt
): AgentAppealed {
  let agentAppealedEvent = changetype<AgentAppealed>(newMockEvent())

  agentAppealedEvent.parameters = new Array()

  agentAppealedEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  agentAppealedEvent.parameters.push(
    new ethereum.EventParam(
      "evidenceCid",
      ethereum.Value.fromString(evidenceCid)
    )
  )
  agentAppealedEvent.parameters.push(
    new ethereum.EventParam(
      "timestamp",
      ethereum.Value.fromUnsignedBigInt(timestamp)
    )
  )

  return agentAppealedEvent
}

export function createAgentRegisteredEvent(
  agentAddress: Address,
  did: string,
  cid: string,
  initScore: BigInt,
  stakeAmount: BigInt
): AgentRegistered {
  let agentRegisteredEvent = changetype<AgentRegistered>(newMockEvent())

  agentRegisteredEvent.parameters = new Array()

  agentRegisteredEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  agentRegisteredEvent.parameters.push(
    new ethereum.EventParam("did", ethereum.Value.fromString(did))
  )
  agentRegisteredEvent.parameters.push(
    new ethereum.EventParam("cid", ethereum.Value.fromString(cid))
  )
  agentRegisteredEvent.parameters.push(
    new ethereum.EventParam(
      "initScore",
      ethereum.Value.fromUnsignedBigInt(initScore)
    )
  )
  agentRegisteredEvent.parameters.push(
    new ethereum.EventParam(
      "stakeAmount",
      ethereum.Value.fromUnsignedBigInt(stakeAmount)
    )
  )

  return agentRegisteredEvent
}

export function createAgentRestoredEvent(
  agentAddress: Address,
  slashed: boolean,
  newTotalPenalty: BigInt,
  newlastMisconductTimestamp: BigInt,
  reason: string
): AgentRestored {
  let agentRestoredEvent = changetype<AgentRestored>(newMockEvent())

  agentRestoredEvent.parameters = new Array()

  agentRestoredEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  agentRestoredEvent.parameters.push(
    new ethereum.EventParam("slashed", ethereum.Value.fromBoolean(slashed))
  )
  agentRestoredEvent.parameters.push(
    new ethereum.EventParam(
      "newTotalPenalty",
      ethereum.Value.fromUnsignedBigInt(newTotalPenalty)
    )
  )
  agentRestoredEvent.parameters.push(
    new ethereum.EventParam(
      "newlastMisconductTimestamp",
      ethereum.Value.fromUnsignedBigInt(newlastMisconductTimestamp)
    )
  )
  agentRestoredEvent.parameters.push(
    new ethereum.EventParam("reason", ethereum.Value.fromString(reason))
  )

  return agentRestoredEvent
}

export function createAgentSlashedEvent(
  agentAddress: Address,
  slashed: boolean,
  penaltyScore: BigInt,
  newTotalPenalty: BigInt,
  slashedEthAmount: BigInt,
  reason: string
): AgentSlashed {
  let agentSlashedEvent = changetype<AgentSlashed>(newMockEvent())

  agentSlashedEvent.parameters = new Array()

  agentSlashedEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  agentSlashedEvent.parameters.push(
    new ethereum.EventParam("slashed", ethereum.Value.fromBoolean(slashed))
  )
  agentSlashedEvent.parameters.push(
    new ethereum.EventParam(
      "penaltyScore",
      ethereum.Value.fromUnsignedBigInt(penaltyScore)
    )
  )
  agentSlashedEvent.parameters.push(
    new ethereum.EventParam(
      "newTotalPenalty",
      ethereum.Value.fromUnsignedBigInt(newTotalPenalty)
    )
  )
  agentSlashedEvent.parameters.push(
    new ethereum.EventParam(
      "slashedEthAmount",
      ethereum.Value.fromUnsignedBigInt(slashedEthAmount)
    )
  )
  agentSlashedEvent.parameters.push(
    new ethereum.EventParam("reason", ethereum.Value.fromString(reason))
  )

  return agentSlashedEvent
}

export function createAgentUnregisteredEvent(
  agentAddress: Address,
  did: string
): AgentUnregistered {
  let agentUnregisteredEvent = changetype<AgentUnregistered>(newMockEvent())

  agentUnregisteredEvent.parameters = new Array()

  agentUnregisteredEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  agentUnregisteredEvent.parameters.push(
    new ethereum.EventParam("did", ethereum.Value.fromString(did))
  )

  return agentUnregisteredEvent
}

export function createMisbehaviorReportedEvent(
  reporter: Address,
  targetAgent: Address,
  evidenceCid: string,
  timestamp: BigInt
): MisbehaviorReported {
  let misbehaviorReportedEvent = changetype<MisbehaviorReported>(newMockEvent())

  misbehaviorReportedEvent.parameters = new Array()

  misbehaviorReportedEvent.parameters.push(
    new ethereum.EventParam("reporter", ethereum.Value.fromAddress(reporter))
  )
  misbehaviorReportedEvent.parameters.push(
    new ethereum.EventParam(
      "targetAgent",
      ethereum.Value.fromAddress(targetAgent)
    )
  )
  misbehaviorReportedEvent.parameters.push(
    new ethereum.EventParam(
      "evidenceCid",
      ethereum.Value.fromString(evidenceCid)
    )
  )
  misbehaviorReportedEvent.parameters.push(
    new ethereum.EventParam(
      "timestamp",
      ethereum.Value.fromUnsignedBigInt(timestamp)
    )
  )

  return misbehaviorReportedEvent
}

export function createRoleAdminChangedEvent(
  role: Bytes,
  previousAdminRole: Bytes,
  newAdminRole: Bytes
): RoleAdminChanged {
  let roleAdminChangedEvent = changetype<RoleAdminChanged>(newMockEvent())

  roleAdminChangedEvent.parameters = new Array()

  roleAdminChangedEvent.parameters.push(
    new ethereum.EventParam("role", ethereum.Value.fromFixedBytes(role))
  )
  roleAdminChangedEvent.parameters.push(
    new ethereum.EventParam(
      "previousAdminRole",
      ethereum.Value.fromFixedBytes(previousAdminRole)
    )
  )
  roleAdminChangedEvent.parameters.push(
    new ethereum.EventParam(
      "newAdminRole",
      ethereum.Value.fromFixedBytes(newAdminRole)
    )
  )

  return roleAdminChangedEvent
}

export function createRoleGrantedEvent(
  role: Bytes,
  account: Address,
  sender: Address
): RoleGranted {
  let roleGrantedEvent = changetype<RoleGranted>(newMockEvent())

  roleGrantedEvent.parameters = new Array()

  roleGrantedEvent.parameters.push(
    new ethereum.EventParam("role", ethereum.Value.fromFixedBytes(role))
  )
  roleGrantedEvent.parameters.push(
    new ethereum.EventParam("account", ethereum.Value.fromAddress(account))
  )
  roleGrantedEvent.parameters.push(
    new ethereum.EventParam("sender", ethereum.Value.fromAddress(sender))
  )

  return roleGrantedEvent
}

export function createRoleRevokedEvent(
  role: Bytes,
  account: Address,
  sender: Address
): RoleRevoked {
  let roleRevokedEvent = changetype<RoleRevoked>(newMockEvent())

  roleRevokedEvent.parameters = new Array()

  roleRevokedEvent.parameters.push(
    new ethereum.EventParam("role", ethereum.Value.fromFixedBytes(role))
  )
  roleRevokedEvent.parameters.push(
    new ethereum.EventParam("account", ethereum.Value.fromAddress(account))
  )
  roleRevokedEvent.parameters.push(
    new ethereum.EventParam("sender", ethereum.Value.fromAddress(sender))
  )

  return roleRevokedEvent
}

export function createServiceUpdatedEvent(
  agentAddress: Address,
  newCid: string
): ServiceUpdated {
  let serviceUpdatedEvent = changetype<ServiceUpdated>(newMockEvent())

  serviceUpdatedEvent.parameters = new Array()

  serviceUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  serviceUpdatedEvent.parameters.push(
    new ethereum.EventParam("newCid", ethereum.Value.fromString(newCid))
  )

  return serviceUpdatedEvent
}

export function createStakeUpdatedEvent(
  agentAddress: Address,
  oldStake: BigInt,
  newStake: BigInt,
  oldInitScore: BigInt,
  newInitScore: BigInt
): StakeUpdated {
  let stakeUpdatedEvent = changetype<StakeUpdated>(newMockEvent())

  stakeUpdatedEvent.parameters = new Array()

  stakeUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "agentAddress",
      ethereum.Value.fromAddress(agentAddress)
    )
  )
  stakeUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "oldStake",
      ethereum.Value.fromUnsignedBigInt(oldStake)
    )
  )
  stakeUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "newStake",
      ethereum.Value.fromUnsignedBigInt(newStake)
    )
  )
  stakeUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "oldInitScore",
      ethereum.Value.fromUnsignedBigInt(oldInitScore)
    )
  )
  stakeUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "newInitScore",
      ethereum.Value.fromUnsignedBigInt(newInitScore)
    )
  )

  return stakeUpdatedEvent
}

export function createTreasuryUpdatedEvent(
  oldTreasury: Address,
  newTreasury: Address
): TreasuryUpdated {
  let treasuryUpdatedEvent = changetype<TreasuryUpdated>(newMockEvent())

  treasuryUpdatedEvent.parameters = new Array()

  treasuryUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "oldTreasury",
      ethereum.Value.fromAddress(oldTreasury)
    )
  )
  treasuryUpdatedEvent.parameters.push(
    new ethereum.EventParam(
      "newTreasury",
      ethereum.Value.fromAddress(newTreasury)
    )
  )

  return treasuryUpdatedEvent
}
