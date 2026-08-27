module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  testMatch: ["**/test/**/*.test.ts"],
  collectCoverage: true,
  collectCoverageFrom: ["src/**/*.ts", "!src/**/*.d.ts", "!src/cli.ts"],
  coverageThreshold: {
    global: {
      lines: 90,
      statements: 90
    }
  }
};
