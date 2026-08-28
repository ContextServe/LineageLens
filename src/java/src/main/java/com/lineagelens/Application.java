package com.lineagelens;

import io.micronaut.configuration.picocli.PicocliRunner;
import picocli.CommandLine;
import picocli.CommandLine.Command;

@Command(
    name = "lineagelens-java",
    description = "LineageLens Java Code Graph Analyzer",
    mixinStandardHelpOptions = true,
    subcommands = {AnalyzeCommand.class}
)
public class Application implements Runnable {

    public static void main(String[] args) {
        if (args.length == 0) {
            args = new String[]{"analyze", "."};
        }
        int exitCode = PicocliRunner.execute(Application.class, args);
        if (exitCode != 0 && !"true".equals(System.getProperty("lineagelens.test"))) {
            System.exit(exitCode);
        }
    }

    @Override
    public void run() {
        CommandLine.usage(this, System.out);
    }
}
