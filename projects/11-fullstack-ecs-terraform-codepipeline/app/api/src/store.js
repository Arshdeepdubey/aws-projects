import crypto from 'node:crypto';

import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import {
  DeleteCommand,
  DynamoDBDocumentClient,
  PutCommand,
  QueryCommand,
  UpdateCommand,
} from '@aws-sdk/lib-dynamodb';

/**
 * DynamoDB-backed store, with an in-memory fallback so `npm run dev` and the
 * unit tests work with no AWS credentials at all.
 */
export function createTodoStore({ tableName = process.env.TODOS_TABLE } = {}) {
  if (!tableName) {
    console.warn(JSON.stringify({ level: 'warn', msg: 'TODOS_TABLE unset — using in-memory store' }));
    return createMemoryStore();
  }

  const client = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
    marshallOptions: { removeUndefinedValues: true },
  });

  return {
    async ping() {
      await client.send(
        new QueryCommand({
          TableName: tableName,
          IndexName: 'owner-createdAt-index',
          KeyConditionExpression: 'ownerId = :owner',
          ExpressionAttributeValues: { ':owner': '__ping__' },
          Limit: 1,
        }),
      );
      return true;
    },

    async list(ownerId, limit) {
      const result = await client.send(
        new QueryCommand({
          TableName: tableName,
          IndexName: 'owner-createdAt-index',
          KeyConditionExpression: 'ownerId = :owner',
          ExpressionAttributeValues: { ':owner': ownerId },
          ScanIndexForward: false,
          Limit: limit,
        }),
      );
      return result.Items ?? [];
    },

    async create({ ownerId, title }) {
      const todo = {
        todoId: crypto.randomUUID(),
        ownerId,
        title,
        done: false,
        createdAt: new Date().toISOString(),
      };
      await client.send(new PutCommand({ TableName: tableName, Item: todo }));
      return todo;
    },

    async setDone(todoId, done) {
      try {
        const result = await client.send(
          new UpdateCommand({
            TableName: tableName,
            Key: { todoId },
            UpdateExpression: 'SET done = :done, updatedAt = :now',
            ConditionExpression: 'attribute_exists(todoId)',
            ExpressionAttributeValues: { ':done': done, ':now': new Date().toISOString() },
            ReturnValues: 'ALL_NEW',
          }),
        );
        return result.Attributes;
      } catch (error) {
        if (error.name === 'ConditionalCheckFailedException') return null;
        throw error;
      }
    },

    async remove(todoId) {
      await client.send(new DeleteCommand({ TableName: tableName, Key: { todoId } }));
    },
  };
}

export function createMemoryStore() {
  const todos = new Map();

  return {
    async ping() {
      return true;
    },
    async list(ownerId, limit) {
      return [...todos.values()]
        .filter((todo) => todo.ownerId === ownerId)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
        .slice(0, limit);
    },
    async create({ ownerId, title }) {
      const todo = {
        todoId: crypto.randomUUID(),
        ownerId,
        title,
        done: false,
        createdAt: new Date().toISOString(),
      };
      todos.set(todo.todoId, todo);
      return todo;
    },
    async setDone(todoId, done) {
      const todo = todos.get(todoId);
      if (!todo) return null;
      todo.done = done;
      todo.updatedAt = new Date().toISOString();
      return todo;
    },
    async remove(todoId) {
      todos.delete(todoId);
    },
  };
}
