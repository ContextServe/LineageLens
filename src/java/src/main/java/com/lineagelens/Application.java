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
        if (args.length > 0 && !args[0].startsWith("-") && !"analyze".equals(args[0]) && !"--help".equals(args[0]) && !"-h".equals(args[0])) {
            // Default sub-command to analyze if directory path is passed directly
            String[] newArgs = new String[args.length + 1];
            newArgs[0] = "analyze";
            System.arraycopy(args, 0, newArgs, 1, args.length);
            args = newArgs;
        } else if (args.length == 0) {
            args = new String[]{"analyze", "."};
        }

        int exitCode = PicocliRunner.execute(Application.class, args);
        System.exit(exitCode);
    }

    @Override
    public void run() {
        CommandLine.usage(this, System.out);
    }
}
