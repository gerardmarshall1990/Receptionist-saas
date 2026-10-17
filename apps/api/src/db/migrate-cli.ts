import { loadConfig } from '../config.js';
import { createPool } from './pool.js';
import { migrate } from './migrate.js';

const pool = createPool(loadConfig().databaseUrl);
const ran = await migrate(pool);
console.log(ran.length ? `applied: ${ran.join(', ')}` : 'up to date');
await pool.end();
