/**
 * Hermes CLI Entry Point
 * 
 * Minimal CLI for running Hermes orchestration jobs.
 * 
 * Usage:
 *   hermes <input> [policyId]
 *   hermes --help
 * 
 * Examples:
 *   hermes "Analyze token 0x123..." trading-default
 *   hermes "What is the status?" read-only
 */

import { runHermesJob } from './jobs/runner';

/**
 * Parse CLI arguments
 */
function parseArgs(): { input: string; policyId: string } {
  const args = process.argv.slice(2);
  
  // Check for help flag
  if (args.includes('--help') || args.includes('-h')) {
    printHelp();
    process.exit(0);
  }
  
  // Input is first non-flag argument
  const input = args[0];
  
  if (!input) {
    console.error('Error: Input message is required');
    printHelp();
    process.exit(1);
  }
  
  // Policy ID is second argument, or default
  const policyId = args[1] || 'trading-default';
  
  return { input, policyId };
}

/**
 * Print help message
 */
function printHelp(): void {
  console.log(`
Hermes - NexFlow Autonomous Orchestrator

Usage:
  hermes <input> [policyId]
  hermes --help

Arguments:
  input     - The message or task for Hermes to process
  policyId  - Policy to use (default: trading-default)

Policies:
  read-only          - Data retrieval only, no trading
  trading-default   - Default trading policy (default)
  trading-conservative - Conservative trading, requires approval
  research          - Deep analysis, no execution
  emergency         - Emergency mode, minimal operations

Examples:
  hermes "Analyze token 0xABC..."
  hermes "What is the status of wallet 0x123...?" read-only
  hermes "Execute trade on 0xDEF..." trading-conservative
`);
}

/**
 * Format tool results for display
 */
function formatToolResults(toolResults: Record<string, any>): string {
  const entries = Object.entries(toolResults);
  
  if (entries.length === 0) {
    return '  (no tool results)';
  }
  
  return entries
    .map(([callId, result]: [string, any]) => {
      const success = result.success ? '✓' : '✗';
      const toolName = result.toolName || callId;
      const duration = result.durationMs ? ` (${result.durationMs}ms)` : '';
      
      let details = '';
      if (result.success && result.data) {
        try {
          const parsed = typeof result.data === 'string' 
            ? JSON.parse(result.data) 
            : result.data;
          details = `\n    ${JSON.stringify(parsed, null, 2).split('\n').join('\n    ')}`;
        } catch {
          details = `\n    ${result.data}`;
        }
      } else if (result.error) {
        details = `\n    Error: ${result.error}`;
      }
      
      return `  ${success} ${toolName}${duration}${details}`;
    })
    .join('\n');
}

/**
 * Main CLI function
 */
async function main(): Promise<void> {
  console.log('\n🤖 Hermes - NexFlow Orchestrator\n');
  console.log('='.repeat(50));
  
  const { input, policyId } = parseArgs();
  
  console.log(`Policy: ${policyId}`);
  console.log(`Input:  ${input}\n`);
  
  console.log('-'.repeat(50));
  
  try {
    const result = await runHermesJob(input, policyId);
    
    console.log('\n' + '='.repeat(50));
    console.log('RESULTS');
    console.log('='.repeat(50));
    
    if (result.success) {
      console.log(`\n✓ Completed in ${result.iterations} iteration(s)\n`);
      
      console.log('Final Response:');
      console.log('-'.repeat(30));
      console.log(result.finalResponse);
      
      console.log('\nTool Results:');
      console.log('-'.repeat(30));
      console.log(formatToolResults(result.toolResults));
    } else {
      console.log(`\n✗ Failed: ${result.error}`);
      process.exit(1);
    }
    
    console.log('\n' + '='.repeat(50));
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    console.error(`\n✗ Fatal error: ${errorMessage}`);
    process.exit(1);
  }
}

// Run the CLI
main().catch((error) => {
  console.error('Unhandled error:', error);
  process.exit(1);
});
