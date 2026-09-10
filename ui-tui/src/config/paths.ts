import { homedir } from 'node:os'
import { join } from 'node:path'

export const getRinCodeHome = (env: NodeJS.ProcessEnv = process.env) => env.RINCODE_HOME?.trim() || join(homedir(), '.rincode')

export const getRinCodeHomeLabel = (env: NodeJS.ProcessEnv = process.env) => env.RINCODE_HOME?.trim() || '~/.rincode'
