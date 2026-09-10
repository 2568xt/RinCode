import { homedir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { getRinCodeHome, getRinCodeHomeLabel } from '../config/paths.js'

describe('RinCode product paths', () => {
  it('uses the RinCode home override for persistent TUI state', () => {
    expect(getRinCodeHome({ RINCODE_HOME: '/tmp/rincode-home' })).toBe('/tmp/rincode-home')
    expect(getRinCodeHomeLabel({ RINCODE_HOME: '/tmp/rincode-home' })).toBe('/tmp/rincode-home')
  })

  it('uses ~/.rincode when the override is empty', () => {
    expect(getRinCodeHome({ RINCODE_HOME: '  ' })).toBe(join(homedir(), '.rincode'))
    expect(getRinCodeHomeLabel({ RINCODE_HOME: '  ' })).toBe('~/.rincode')
  })
})
