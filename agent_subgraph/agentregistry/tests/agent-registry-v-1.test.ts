import {
  assert,
  describe,
  test,
  clearStore,
  beforeAll,
  afterAll
} from "matchstick-as/assembly/index"
import { Address, BigInt, Bytes } from "@graphprotocol/graph-ts"
import { AgentAppealed } from "../generated/schema"
import { AgentAppealed as AgentAppealedEvent } from "../generated/AgentRegistry_v1/AgentRegistry_v1"
import { handleAgentAppealed } from "../src/agent-registry-v-1"
import { createAgentAppealedEvent } from "./agent-registry-v-1-utils"

// Tests structure (matchstick-as >=0.5.0)
// https://thegraph.com/docs/en/subgraphs/developing/creating/unit-testing-framework/#tests-structure

describe("Describe entity assertions", () => {
  beforeAll(() => {
    let agentAddress = Address.fromString(
      "0x0000000000000000000000000000000000000001"
    )
    let evidenceCid = "Example string value"
    let timestamp = BigInt.fromI32(234)
    let newAgentAppealedEvent = createAgentAppealedEvent(
      agentAddress,
      evidenceCid,
      timestamp
    )
    handleAgentAppealed(newAgentAppealedEvent)
  })

  afterAll(() => {
    clearStore()
  })

  // For more test scenarios, see:
  // https://thegraph.com/docs/en/subgraphs/developing/creating/unit-testing-framework/#write-a-unit-test

  test("AgentAppealed created and stored", () => {
    assert.entityCount("AgentAppealed", 1)

    // 0xa16081f360e3847006db660bae1c6d1b2e17ec2a is the default address used in newMockEvent() function
    assert.fieldEquals(
      "AgentAppealed",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "agentAddress",
      "0x0000000000000000000000000000000000000001"
    )
    assert.fieldEquals(
      "AgentAppealed",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "evidenceCid",
      "Example string value"
    )
    assert.fieldEquals(
      "AgentAppealed",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "timestamp",
      "234"
    )

    // More assert options:
    // https://thegraph.com/docs/en/subgraphs/developing/creating/unit-testing-framework/#asserts
  })
})
